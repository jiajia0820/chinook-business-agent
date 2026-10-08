"""Opt-in C DemoJSONModel + shipped real SQLite snapshot, with no model fallback."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ..adapters.bounded_executor import BoundedExecutor
from ..adapters.c_sql import build_c_request, create_c_sql_spec, normalize_c_response
from ..contracts import ApiError
from ..core.errors import ApplicationError
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolRuntime
from ..tools.models import InternalModel, SqlTaskOutcome, SqlTaskRequest, ToolFailure


VENDOR_ROOT = Path(__file__).resolve().parents[4] / "third_party" / "chinook_c"
ARCHIVE_SHA256 = "74748f209e042eab3bc446491e6b6ad7ccf5138e5dad93b79221dfa55541e27d"
DATABASE_SHA256 = "adb73b5b0ea57598926fa8a72ffb2848c64f7f17a4e57b5e0c04a182e24f565c"
SERVICE_SHA256 = "04f4369b3e7432d8d77624e0f41683545fc7aea93afca9b5f5c06eedd58768b2"
REQUIRED_ARTIFACTS = frozenset({
    "backend/__init__.py", "backend/sql_module/__init__.py", "backend/sql_module/__main__.py",
    "backend/sql_module/adapters/__init__.py", "backend/sql_module/adapters/sqlite.py",
    "backend/sql_module/adapters/postgresql.py", "backend/sql_module/catalog.py",
    "backend/sql_module/errors.py", "backend/sql_module/execution.py", "backend/sql_module/generation.py",
    "backend/sql_module/profiles.py", "backend/sql_module/schema.py", "backend/sql_module/service.py",
    "backend/sql_module/validation.py", "profiles/chinook/profile.json", "profiles/chinook/metrics.json",
    "data/chinook/Chinook.db", "data/chinook/LICENSE.md", "requirements-c.txt",
})
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class ArtifactFile(InternalModel):
    path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)


class ArtifactManifest(InternalModel):
    snapshot_date: str
    source_archive: str
    archive_sha256: Sha256
    selection: str
    files: list[ArtifactFile] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_files(self):
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate manifest paths")
        return self


def _startup_error(reason: str):
    return ApplicationError(status_code=503, error=ApiError(
        code="PROFILE_UNAVAILABLE", message="C 离线 SQL 集成初始化失败，未回退到模拟成功结果。",
        retryable=False, details={"reason": reason},
    ))


def verify_vendor(root: Path) -> ArtifactManifest:
    root = root.resolve()
    manifest = ArtifactManifest.model_validate_json((root / "artifact-manifest.json").read_text(encoding="utf-8"))
    if manifest.archive_sha256 != ARCHIVE_SHA256:
        raise ValueError("archive version changed")
    files = {file.path: file for file in manifest.files}
    if set(files) != REQUIRED_ARTIFACTS:
        raise ValueError("manifest selection changed")
    if files["data/chinook/Chinook.db"].sha256 != DATABASE_SHA256 or files["backend/sql_module/service.py"].sha256 != SERVICE_SHA256:
        raise ValueError("handoff version changed")
    for record in files.values():
        path = (root / record.path).resolve()
        path.relative_to(root)  # Reject traversal/symlink escape in server-owned manifest.
        if not path.is_file() or path.stat().st_size != record.size_bytes:
            raise ValueError("artifact missing or changed")
        with path.open("rb") as stream:
            actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual_hash != record.sha256:
            raise ValueError("artifact hash changed")
    return manifest


def _load_backend(root: Path, profiles: ProfileRegistry, tables: frozenset[str] | None):
    if root != VENDOR_ROOT:
        verify_vendor(VENDOR_ROOT)  # Resource override never redirects Python imports.
    verify_vendor(root)
    # The only executable module location is the bundled namespace; a custom
    # trusted root selects resources, not arbitrary Python code for import.
    from third_party.chinook_c.backend.sql_module.catalog import load_catalog
    from third_party.chinook_c.backend.sql_module.generation import DemoJSONModel
    from third_party.chinook_c.backend.sql_module.profiles import AccessContext, Registry
    from third_party.chinook_c.backend.sql_module.service import NLQueryService

    registry = Registry.load(root / "profiles")
    backend = registry.profiles["chinook"]
    if backend.data["mode"] != "sql_only" or backend.database["dialect"] != "sqlite":
        raise ValueError("unexpected backend mode")
    if Path(backend.connection_url().database).resolve() != (root / "data/chinook/Chinook.db").resolve():
        raise ValueError("unexpected database path")
    allowed = frozenset(backend.database["allowed_tables"])
    if tables is not None and (not isinstance(tables, frozenset) or not tables <= allowed):
        raise ValueError("invalid trusted table configuration")
    access = AccessContext({"chinook": allowed if tables is None else tables})
    service = NLQueryService(registry, model=DemoJSONModel())
    snapshot = service.schemas.get_schema("chinook", access)
    catalog = load_catalog(backend, snapshot)
    profile = profiles.get("chinook-music")
    registered = {metric["metric_id"] for metric in catalog["metrics"]}
    if not profile.supports("sql") or not profile.sql_backend or profile.sql_backend.profile_id != "chinook" or profile.sql_backend.model_mode != "offline" or set(profile.sql_backend.registered_metric_ids) != registered or profile.sql_backend.declared_slots != backend.data.get("slots", {}):
        raise ValueError("B/C profile mapping drift")
    for metric in catalog["metrics"]:
        if metric.get("unit") != profile.metric(metric["metric_id"]).unit:
            raise ValueError("B/C unit drift")
    return service, access, {"backend_profile_id": "chinook", "model_mode": "offline", "database_sha256": DATABASE_SHA256, "schema_version": snapshot["schema_version"], "table_count": len(snapshot["tables"])}


class OfflineCSqlAdapter:
    def __init__(self, profiles, service, access, executor, provenance):
        self.profiles, self._service, self._access, self.executor, self.provenance = profiles, service, access, executor, provenance

    async def run(self, task: SqlTaskRequest, context: ToolContext) -> SqlTaskOutcome:
        profile = self.profiles.get(context.profile_id)
        if not profile.sql_backend or profile.sql_backend.model_mode != "offline" or profile.sql_backend.profile_id != "chinook":
            raise ToolFailure(ApiError(code="UNSUPPORTED_CAPABILITY", message="此适配器仅支持已确认的 chinook 离线后端。", retryable=False))
        request = build_c_request(task, request_id=context.request_id, profile=profile)
        raw = await self.executor.run(lambda: self._service.answer_sql(request.payload(), self._access), deadline=context.deadline)
        outcome = normalize_c_response(raw, task=task, request_id=context.request_id, profile=profile)
        outcome.limitations.append("3C-1：C 离线固定三问 + 真实 Chinook 样例库；不是联网模型或完整自由问法能力。")
        if any(result.source.tables == ["main.Genre"] for result in outcome.sql_results):
            outcome.limitations.append("固定类型查询列出整个 Genre 表，含影视分类；不是仅音频可销售风格清单。")
        if outcome.status == "success":
            outcome.limitations.append("展示的 SQL 是 C 已接受候选；执行前 C 会进行只读校验、字段限定与 SQL 规范化，不代表逐字相同的驱动语句。")
        outcome.diagnostics["integration"] = dict(self.provenance)
        return outcome


@dataclass
class OfflineSqlIntegration:
    profiles: ProfileRegistry
    runtime: ToolRuntime
    sql_tool: OfflineCSqlAdapter

    def create_graph_service(self):
        from ..services.graph_ask_service import GraphAskService
        return GraphAskService(self.profiles, self.runtime, execution_mode="offline_sql_integration")

    def create_formal_graph_service(self):
        """3C-4 opt-in; does not replace the default API or historical demo."""
        from ..response import FormalResponseComposer
        from ..services.graph_ask_service import GraphAskService
        composer = FormalResponseComposer.for_sql(self.profiles.get("chinook-music"))
        return GraphAskService(self.profiles, self.runtime, response_composer=composer, execution_mode="offline_sql_integration")

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        return await self.sql_tool.executor.aclose(grace_seconds=grace_seconds)


async def create_offline_sql_integration(*, profiles: ProfileRegistry | None = None, vendor_root: Path | None = None, trusted_tables: frozenset[str] | None = None, max_workers: int = 2, timeout_ms: int = 60000) -> OfflineSqlIntegration:
    """Trusted server factory only. No API, user_role, HTTP model or RAG wiring."""
    executor = None
    try:
        if profiles is None:
            profile = ProfileRegistry.defaults().get("chinook-music")
            # Keep the public hybrid identity, but advertise only the capability
            # actually installed in this independently-created 3C-1 instance.
            profile.capabilities = ["sql"]
            profile.document_root = None
            profile.config_version += "+3c1-sql"
            profile.limitations.append("此 3C-1 实例只装配 SQL；RAG 与计算尚未可用。")
            profiles = ProfileRegistry([profile])
        root = (vendor_root or VENDOR_ROOT).resolve()
        executor = BoundedExecutor(max_workers)
        service, access, provenance = await executor.run(lambda: _load_backend(root, profiles, trusted_tables))
        adapter = OfflineCSqlAdapter(profiles, service, access, executor, provenance)
        runtime = ToolRuntime(profiles)
        runtime.register(create_c_sql_spec(adapter.run, profiles, timeout_ms=timeout_ms))
        return OfflineSqlIntegration(profiles, runtime, adapter)
    except BaseException as exc:
        if executor is not None:
            await executor.aclose()
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) or not isinstance(exc, Exception):
            raise
        raise _startup_error("DEPENDENCY_MISSING" if isinstance(exc, ImportError) else "CONFIG_OR_ARTIFACT_INVALID") from None
