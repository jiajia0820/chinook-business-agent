"""Bounded local SQL eligibility; C still owns semantic and execution checks."""

import os
import re


FAST_SQL_METRICS = {"customer_count", "sales_amount", "units_sold", "order_count", "purchasing_customers", "track_attributes"}
SQL_METADATA_SLOTS = {"year", "quarter", "month", "period_grain", "scope", "data_scope", "genre_boundary_resolved", "year_assumed"}


def fast_sql_enabled():
    return os.environ.get("AGENT_FAST_SQL", "on").strip().casefold() in {"on", "1", "true", "yes"}


def fast_sql_eligible(task, profile):
    """Shape preflight for either a BusinessTask or its SqlTaskRequest.

    True only permits C's answer_registered attempt; it does not certify raw
    question coverage.  C must return None for any unconsumed filter or shape.
    """
    backend = profile.sql_backend
    if not backend or backend.profile_id != "chinook" or not profile.supports("sql"):
        return False
    if getattr(task, "route", "sql") != "sql" or getattr(task, "needs_calculation", False):
        return False
    if any(getattr(task, field, None) for field in ("excluded_entities", "time_issues", "semantic_blockers")):
        return False
    ir = getattr(task, "question_ir", None)
    if ir is not None and (ir.exclusions or ir.clarification_blockers):
        return False
    if set(getattr(task, "required_outputs", [])) - {"actual"}:
        return False
    metrics = getattr(task, "business_metric_ids", None)
    if metrics is None:
        metrics = task.metric_ids
    if not metrics or set(metrics) - FAST_SQL_METRICS or not set(metrics) <= set(backend.registered_metric_ids):
        return False
    if "customer_count" in metrics and metrics != ["customer_count"] or "track_attributes" in metrics and metrics != ["track_attributes"]:
        return False
    slots = getattr(task, "slots", None)
    if slots is None:
        slots = task.resolved_slots
    if set(slots) - (set(backend.declared_slots) | SQL_METADATA_SLOTS):
        return False
    if set(slots) & {"comparison_period", "result_grain", "playlist_id", "track_ids"}:
        return False
    intent = slots.get("query_intent", {})
    allowed = {"operation", "track_ids"} if metrics == ["track_attributes"] else {"measures"}
    return isinstance(intent, dict) and not set(intent) - allowed


def fast_sql_question(question, profile):
    """Canonicalize only server-registered aliases, retaining all other text."""
    aliases = {alias.alias: alias.label for alias in profile.aliases}
    if not aliases:
        return question
    pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    folded = {alias.casefold(): label for alias, label in aliases.items()}
    return re.sub(pattern, lambda match: folded[match[0].casefold()], question, flags=re.I)
