"""Opt-in C DemoJSONModel + shipped real SQLite snapshot, with no model fallback."""

from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ..adapters.bounded_executor import BoundedExecutor
from ..adapters.c_sql import build_c_request, c_supports_query_obligations, create_c_sql_spec, normalize_c_response
from ..contracts import ApiError
from ..core.errors import ApplicationError
from ..profiles import ProfileRegistry
from ..runtime import ToolContext, ToolRuntime
from ..tools.models import InternalModel, SqlTaskOutcome, SqlTaskRequest, ToolFailure


VENDOR_ROOT = Path(__file__).resolve().parents[4] / "third_party" / "chinook_c"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
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


def _load_backend(root: Path, profiles: ProfileRegistry, tables: frozenset[str] | None,
                  model_mode: str = "offline", canonical: bool = False,
                  profile_ids: tuple[str, ...] | None = None):
    selected_ids = profile_ids or profiles.ids()
    if canonical:
        for public_id in selected_ids:
            public = profiles.get(public_id)
            backend_id = public.sql_backend.profile_id if public.sql_backend else None
            required = (root / "profiles" / str(backend_id) / "profile.json",
                        root / "profiles" / str(backend_id) / "metrics.json")
            if backend_id == "chinook":
                required += (root / "data/chinook/Chinook.db",)
            if any(not path.is_file() for path in required):
                raise ValueError("canonical backend artifact missing")
    else:
        if root != VENDOR_ROOT:
            verify_vendor(VENDOR_ROOT)
        verify_vendor(root)
    # The only executable module location is the bundled namespace; a custom
    # trusted root selects resources, not arbitrary Python code for import.
    if canonical:
        from backend.sql_module.catalog import load_catalog
        from backend.sql_module.generation import DemoJSONModel, HTTPJSONModel
        from backend.sql_module.profiles import AccessContext, Registry
        from backend.sql_module.service import NLQueryService
    else:
        from third_party.chinook_c.backend.sql_module.catalog import load_catalog
        from third_party.chinook_c.backend.sql_module.generation import DemoJSONModel
        from backend.sql_module.generation import HTTPJSONModel
        from third_party.chinook_c.backend.sql_module.profiles import AccessContext, Registry
        from third_party.chinook_c.backend.sql_module.service import NLQueryService

    registry = Registry.load(root / "profiles")
    access_tables = {}
    for public_id in selected_ids:
        public = profiles.get(public_id)
        backend_id = public.sql_backend.profile_id if public.sql_backend else None
        if backend_id not in registry.profiles:
            raise ValueError("backend profile missing")
        backend = registry.profiles[backend_id]
        if backend.data["mode"] != "sql_only":
            raise ValueError("unexpected backend mode")
        allowed = frozenset(backend.database["allowed_tables"])
        selected_tables = tables if len(selected_ids) == 1 and tables is not None else allowed
        if not isinstance(selected_tables, frozenset) or not selected_tables <= allowed:
            raise ValueError("invalid trusted table configuration")
        access_tables[backend_id] = selected_tables
    access = AccessContext(access_tables)
    if model_mode == "live":
        service = NLQueryService(registry, model=HTTPJSONModel.from_env())
    elif model_mode == "offline":
        service = NLQueryService(registry, model=DemoJSONModel())
    else:
        raise ValueError("unsupported model mode")
    provenance = {"model_mode": model_mode, "profiles": {}}
    for public_id in selected_ids:
        public = profiles.get(public_id)
        backend_id = public.sql_backend.profile_id
        backend = registry.profiles[backend_id]
        snapshot = service.schemas.get_schema(backend_id, access)
        catalog = load_catalog(backend, snapshot)
        registered = {metric["metric_id"] for metric in catalog["metrics"]}
        declared = {name: rule.model_dump(mode="python", exclude_none=True, exclude_defaults=True) for name, rule in public.sql_backend.declared_slots.items()}
        if (not public.supports("sql") or public.sql_backend.model_mode != model_mode
                or set(public.sql_backend.registered_metric_ids) != registered
                or declared != backend.data.get("slots", {})):
            raise ValueError("B/C profile mapping drift")
        for metric in catalog["metrics"]:
            configured = public.metric(metric["metric_id"])
            if configured is None or metric.get("unit") != configured.unit:
                raise ValueError("B/C unit drift")
        record = {"backend_profile_id": backend_id, "schema_version": snapshot["schema_version"], "table_count": len(snapshot["tables"])}
        if backend_id == "chinook":
            record["database_sha256"] = DATABASE_SHA256
        provenance["profiles"][public_id] = record
    if selected_ids == ("chinook-music",) and "chinook-music" in provenance["profiles"]:
        provenance.update(provenance["profiles"]["chinook-music"])
        provenance["profiles"] = {"chinook-music": provenance["profiles"]["chinook-music"]}
    return service, access, provenance


class OfflineCSqlAdapter:
    def __init__(self, profiles, service, access, executor, provenance):
        self.profiles, self._service, self._access, self.executor, self.provenance = profiles, service, access, executor, provenance
        # 3C-1 走 third_party 封存快照，canonical 走仓库内 backend；只有后者认识
        # query_obligations。装配时探测一次，避免每轮把未知键发给旧 C 校验器。
        self._supports_query_obligations = c_supports_query_obligations(service)

    async def run(self, task: SqlTaskRequest, context: ToolContext) -> SqlTaskOutcome:
        profile = self.profiles.get(context.profile_id)
        if not profile.sql_backend or profile.sql_backend.model_mode not in {"offline", "live"}:
            raise ToolFailure(ApiError(code="UNSUPPORTED_CAPABILITY", message="当前 profile 没有已确认的 SQL 后端。", retryable=False))
        request = build_c_request(task, request_id=context.request_id, profile=profile,
                                  supports_query_obligations=self._supports_query_obligations)
        from ..agent.fast_path import controlled_sql_eligible, fast_sql_enabled, fast_sql_eligible, fast_sql_question

        def answer():
            registered = getattr(self._service, "answer_registered", None)
            if callable(registered) and fast_sql_enabled():
                # Keep unknown words from the original question visible to
                # C's closed scope check; model input remains the validated
                # normalized request used by the existing live chain.
                fast_request = request.payload()
                fast_request["question"] = fast_sql_question(task.question, profile)
                supports_controlled = "use_controlled_plan" in inspect.signature(registered).parameters
                if supports_controlled and controlled_sql_eligible(task, profile):
                    raw = registered(fast_request, self._access, use_controlled_plan=True)
                    if raw is not None:
                        return raw, True
                if fast_sql_eligible(task, profile):
                    raw = registered(fast_request, self._access)
                    if raw is not None:
                        return raw, True
            return self._service.answer_sql(request.payload(), self._access), False

        raw, registered_used = await self.executor.run(answer, deadline=context.deadline)
        outcome = normalize_c_response(raw, task=task, request_id=context.request_id, profile=profile)
        if profile.sql_backend.model_mode == "live":
            outcome.limitations.append("C 配置为 live 模式；实际使用模型候选或受控修复的情况见本轮限制与工具追踪，接受的 SQL 须通过只读、表字段与参数安全校验。")
        elif registered_used:
            outcome.limitations.append("C 配置为 offline 模式；本轮执行已注册受控 SQL，未调用离线演示模型。")
        else:
            outcome.limitations.append("3C-1：C 离线固定三问 + 真实 Chinook 样例库；不是联网模型或完整自由问法能力。")
        if registered_used and "media_type_sales" in task.metric_ids:
            outcome.limitations.append("媒体类型金额的日期过滤存在可空订单连接风险；改用已登记指标的明细日期受控查询计划。")
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
        profile_id = self.profiles.ids()[0]
        composer = FormalResponseComposer.for_sql(self.profiles.get(profile_id))
        return GraphAskService(self.profiles, self.runtime, response_composer=composer, execution_mode="offline_sql_integration")

    async def aclose(self, *, grace_seconds: float = 10) -> bool:
        return await self.sql_tool.executor.aclose(grace_seconds=grace_seconds)


async def create_offline_sql_integration(*, profiles: ProfileRegistry | None = None, vendor_root: Path | None = None, trusted_tables: frozenset[str] | None = None, max_workers: int = 2, timeout_ms: int = 60000, model_mode: str = "offline") -> OfflineSqlIntegration:
    """Trusted server factory only. No API, user_role, HTTP model or RAG wiring."""
    executor = None
    try:
        if profiles is None:
            profile = ProfileRegistry.stage1().get("chinook-music")
            profile.sql_backend.model_mode = model_mode
            # Keep the public hybrid identity, but advertise only the capability
            # actually installed in this independently-created 3C-1 instance.
            profile.capabilities = ["sql"]
            profile.document_root = None
            profile.config_version += "+3c1-sql"
            profile.limitations.append("此 3C-1 实例只装配 SQL；RAG 与计算尚未可用。")
            profiles = ProfileRegistry([profile])
        root = (vendor_root or VENDOR_ROOT).resolve()
        executor = BoundedExecutor(max_workers)
        service, access, provenance = await executor.run(lambda: _load_backend(root, profiles, trusted_tables, model_mode))
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


async def create_canonical_sql_integration(*, profiles: ProfileRegistry | None = None,
    profile_ids: tuple[str, ...] | None = None,
    trusted_tables: frozenset[str] | None = None, max_workers: int = 2,
    timeout_ms: int = 60000, model_mode: str = "offline") -> OfflineSqlIntegration:
    executor = None
    try:
        if profiles is None:
            profiles = ProfileRegistry.defaults().select(profile_ids or ("chinook-music",))
        selected = profile_ids or profiles.ids()
        profiles = profiles.select(selected)
        configured = []
        for profile_id in profiles.ids():
            profile = profiles.get(profile_id)
            profile.sql_backend.model_mode = model_mode
            configured.append(profile)
        profiles = ProfileRegistry(configured)
        root = PROJECT_ROOT.resolve()
        executor = BoundedExecutor(max_workers)
        service, access, provenance = await executor.run(
            lambda: _load_backend(root, profiles, trusted_tables, model_mode, canonical=True, profile_ids=selected)
        )
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
