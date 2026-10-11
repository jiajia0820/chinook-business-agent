"""Approved cross-source joins, with independent scopes and immutable facts."""
from decimal import Decimal
import re

from pydantic import Field, JsonValue
from ..tools.models import InternalModel


class BusinessSubtask(InternalModel):
    task_id: str
    kind: str
    fact_role: str
    period: dict[str, JsonValue] = Field(default_factory=dict)
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    grain: str
    depends_on: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    source_policy: str = "this_turn_verified"
    completion_rule: str = "all_required_fields"


def prepare(task, chunks, snapshots):
    task = task.model_copy(deep=True)
    if not ({"transaction_exists", "customer_tiers"} & set(task.required_outputs)):
        return task
    if not chunks or not snapshots:
        return None
    known = {c.chunk_id: c for c in snapshots}
    if any(c.chunk_id not in known or (c.doc_id, c.text, c.source_uri) != (known[c.chunk_id].doc_id, known[c.chunk_id].text, known[c.chunk_id].source_uri) for c in chunks):
        return None
    if any(not c["complete"] for c in task.evidence_coverage):
        return None
    if "customer_tiers" in task.required_outputs:
        if len(task.entities) != 1 or task.entities[0]["type"] != "genre":
            return None
        task.slots["result_grain"] = "customer_audio"
        task.business_metric_ids = ["sales_amount"]
        return task
    if len(task.entities) != 1 or task.entities[0]["type"] != "genre":
        return None
    label = task.entities[0]["label"]
    ids = []
    for chunk in chunks:
        if chunk.doc_id != "D05":
            continue
        # Take only the source paragraph for the confirmed canonical genre.
        match = re.search(re.escape(label) + r".*?成交候选.*?(?=这些商品|上述商品|\n[五六七八] |$)", chunk.text, re.S)
        if match:
            ids.extend(int(n) for n in re.findall(r"(\d+)\s*《", match.group()))
    interval = task.slots.get("requested_track_range")
    if interval:
        ids = [i for i in ids if interval[0] <= i <= interval[1]]
    ids = sorted(set(ids))
    if not ids or len(ids) > 20:
        return None
    task.slots.update(result_grain="track_set", track_ids=ids)
    task.business_metric_ids = ["track_sales"]
    task.slot_provenance["track_ids"] = {"source": "verified_D05_intersection", "confirmed": True,
        "source_refs": [c.chunk_id for c in chunks if c.doc_id == "D05"]}
    return task


def thresholds(chunks):
    values = []
    for c in chunks:
        if c.doc_id == "D04":
            match = re.search(r"A\s*\|[^\n]*大于等于\s*(\d+(?:\.\d+)?)", c.text)
            lower = re.search(r"B\s*\|[^\n]*?\|\s*(\d+(?:\.\d+)?)\s*至", c.text)
            if match and lower:
                values.append((Decimal(match[1]), Decimal(lower[1]), c.chunk_id))
    unique = {(a, b) for a, b, _ in values}
    return values[0] if len(unique) == 1 else None


def completed(task, sql, chunks):
    if not sql or sql.status != "success" or not sql.sql_results or any(r.truncated for r in sql.sql_results):
        return False
    rows = sql.sql_results[0].rows
    if "customer_tiers" in task.required_outputs:
        return bool(thresholds(chunks)) and bool(rows) and all({"CustomerId", "selected_genre_amount", "sales_amount", "order_count"} <= set(r) and r["sales_amount"] >= r["selected_genre_amount"] > 0 for r in rows)
    if "transaction_exists" in task.required_outputs:
        return set(task.slots.get("track_ids", [])) == {r.get("TrackId") for r in rows} and all({"GenreId", "MediaTypeId", "sales_amount", "units_sold"} <= set(r) for r in rows)
    if "monthly_actuals" in task.required_outputs:
        y, q = task.slots.get("year"), task.slots.get("quarter")
        expected = {f"{y}-{m:02}" for m in range((q - 1) * 3 + 1, q * 3 + 1)} if y and q else set()
        return bool(expected) and expected == {r.get("month") for r in rows} and len(rows) == len(expected)
    return True
