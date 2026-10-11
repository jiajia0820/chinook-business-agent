"""Offline/shadow-only semantic proposal validation; never invokes tools/LLMs."""
from typing import Literal

from pydantic import Field
from ..tools.models import InternalModel
from .turn_update import TaskPatch

ENABLED_FOR_EXECUTION = False
ALLOWED = {"year", "quarter", "month", "period_grain", "scope", "data_scope", "genre_boundary_resolved", "comparison_period"}


class SemanticProposal(InternalModel):
    goal: str = Field(min_length=1, max_length=100)
    patch: TaskPatch | None = None
    source_roles: list[Literal["database_actual", "reported_actual", "target", "plan", "document_rule", "advice"]] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list, max_length=8)


def audit_proposal(payload, task):
    try:
        proposal = SemanticProposal.model_validate(payload)
        patch = proposal.patch
        if patch:
            if task is None or (patch.task_id, patch.expected_version) != (task.task_id, task.task_version):
                raise ValueError("stale_patch")
            if not (set(patch.set_slots) | set(patch.clear_slots)) <= ALLOWED:
                raise ValueError("unknown_slot")
            for name, maximum in (("year", 9998), ("quarter", 4), ("month", 12)):
                if name in patch.set_slots and (type(patch.set_slots[name]) is not int or not 1 <= patch.set_slots[name] <= maximum):
                    raise ValueError("invalid_period")
            for name in ("scope", "data_scope"):
                if name in patch.set_slots and (not isinstance(patch.set_slots[name], str) or len(patch.set_slots[name]) > 200):
                    raise ValueError("invalid_scope")
            if "comparison_period" in patch.set_slots:
                period = patch.set_slots["comparison_period"]
                if not isinstance(period, dict) or not set(period) <= {"year", "quarter"}:
                    raise ValueError("invalid_comparison")
                for name, maximum in (("year", 9998), ("quarter", 4)):
                    if name in period and (type(period[name]) is not int or not 1 <= period[name] <= maximum):
                        raise ValueError("invalid_comparison")
        return {"valid": True, "execution_enabled": False, "proposal": proposal.model_dump(mode="json")}
    except Exception:
        return {"valid": False, "execution_enabled": False, "reason": "invalid_or_stale_proposal"}
