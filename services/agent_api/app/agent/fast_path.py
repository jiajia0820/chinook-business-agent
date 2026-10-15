"""Bounded local SQL eligibility; C still owns semantic and execution checks."""

import os
import re


FAST_SQL_METRICS = {"customer_count", "sales_amount", "units_sold", "order_count", "purchasing_customers", "mixed_order_count", "track_attributes"}
CONTROLLED_SQL_DIMENSIONS = {"genre", "track", "album", "artist", "media_type", "billing_country", "customer", "support_rep", "playlist", "month"}
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
    if set(getattr(task, "required_outputs", [])) - {"actual", "support_distribution"}:
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
    if metrics == ["track_attributes"]:
        allowed = {"operation", "track_ids"}
    elif metrics == ["mixed_order_count"] and intent.get("operation") == "mixed_media_orders":
        allowed = {"operation"}
    elif intent.get("dimension") == "support_rep" and metrics == ["purchasing_customers"]:
        allowed = {"dimension", "measures", "order_by", "order_desc"}
    else:
        allowed = {"measures"}
    return isinstance(intent, dict) and not set(intent) - allowed


def fast_sql_question(question, profile):
    """Canonicalize only server-registered aliases, retaining all other text."""
    aliases = {alias.alias: alias.label for alias in profile.aliases}
    if not aliases:
        return question
    pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    folded = {alias.casefold(): label for alias, label in aliases.items()}
    return re.sub(pattern, lambda match: folded[match[0].casefold()], question, flags=re.I)


def controlled_sql_eligible(task, profile):
    """Allow the canonical registered plan before the model chain.

    This is deliberately broader than ``fast_sql_eligible``: the latter keeps
    the historical text-closed fast path, while this gate trusts only the
    already parsed query intent and C's registered controlled-query builder.
    The adapter still treats a ``None`` plan as a model fallback.
    """
    backend = profile.sql_backend
    if not backend or backend.profile_id != "chinook" or not profile.supports("sql"):
        return False
    if getattr(task, "route", "sql") != "sql" or getattr(task, "needs_calculation", False):
        return False
    if any(getattr(task, field, None) for field in ("excluded_entities", "time_issues", "semantic_blockers")):
        return False
    obligations = getattr(task, "query_obligations", {}) or {}
    if obligations.get("unresolved_terms") or obligations.get("requires_running_total"):
        return False
    metrics = list(getattr(task, "business_metric_ids", None) or getattr(task, "metric_ids", []))
    if not metrics or not set(metrics) <= set(backend.registered_metric_ids):
        return False
    slots = getattr(task, "slots", None)
    if slots is None:
        slots = task.resolved_slots
    if set(slots) & {"comparison_period", "playlist_id", "track_ids"}:
        return False
    intent = slots.get("query_intent", {})
    if not isinstance(intent, dict):
        return False
    if "query_intent" not in slots:
        return False
    if intent.get("operation") in {"track_attributes", "mixed_media_orders"}:
        return True
    dimension = intent.get("dimension")
    if dimension not in CONTROLLED_SQL_DIMENSIONS:
        return set(intent) <= {"measures"} and set(metrics) <= FAST_SQL_METRICS
    return True
