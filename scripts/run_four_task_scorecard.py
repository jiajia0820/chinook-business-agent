"""Score four-task evaluation reports without loading gold-standard data."""
from __future__ import annotations

import argparse
import json
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any


class ScorecardError(ValueError):
    """Raised when an input report cannot be scored safely."""


SUITE_ALIASES = OrderedDict(
    (
        ("entity_rewrite", ("entity_rewrite", "entity-rewrite", "entity", "rewrite", "alias", "special-alias", "eval_special_alias", "eval_entity_rewrite")),
        ("formula", ("formula", "calculation", "calc", "special-formula", "eval_special_formula", "eval_formula")),
        ("cross_source", ("cross_source", "cross-source", "cross", "eval_cross_source")),
        ("clarification", ("clarification", "clarify", "special-clarify", "special-clarification", "eval_special_clarification", "eval_clarification")),
        ("multiturn", ("multiturn", "multi_turn", "multi-turn", "session", "eval_multiturn")),
        ("regression", ("regression", "regress", "eval_regression", "c-regression")),
    )
)


def _suite(record: dict[str, Any]) -> str:
    for key in ("suite", "capability", "group"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip().lower().replace(" ", "_")
            for canonical, aliases in SUITE_ALIASES.items():
                if candidate == canonical or candidate in aliases:
                    return canonical
    identifier = str(record.get("id", "")).lower()
    # Check explicit, longer aliases first through the ordered mapping. This keeps
    # clarification and multiturn as separate denominators even when both appear
    # in a report.
    for canonical, aliases in SUITE_ALIASES.items():
        if any(identifier.startswith(alias) for alias in aliases):
            return canonical
    return "other"


def _status(record: dict[str, Any]) -> tuple[bool, str | None]:
    if "business_passed" in record and isinstance(record["business_passed"], bool):
        return record["business_passed"], "business_passed"
    if "passed" in record and isinstance(record["passed"], bool):
        return record["passed"], "passed"
    return False, None


def _problems(record: dict[str, Any]) -> list[str]:
    values = record.get("problems", record.get("failure_reasons", record.get("reasons", [])))
    if isinstance(values, str):
        return [values]
    if isinstance(values, list):
        return [str(value) for value in values if value not in (None, "")]
    return []


def score_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a conservative scorecard for an evaluation report.

    ``business_passed`` is preferred when present; old reports using ``passed``
    remain valid. Missing per-record status is always a failure. ``overall`` is
    the live pass count (business passes minus records pending external API),
    while ``business_passed`` retains the business result for later re-testing.
    """
    if not isinstance(report, dict):
        raise ScorecardError("报告必须是 JSON 对象")
    records = report.get("results", report.get("records"))
    if not isinstance(records, list):
        raise ScorecardError("报告缺少 results/records 列表")
    total = report.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        raise ScorecardError("报告 total 必须是整数")
    if total != len(records):
        raise ScorecardError(f"total={total} 与 records={len(records)} 不一致")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise ScorecardError(f"第 {index + 1} 条记录不是对象")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ScorecardError(f"第 {index + 1} 条记录缺少 id")
        identifier = identifier.strip()
        if identifier in seen:
            raise ScorecardError(f"重复 id: {identifier}")
        seen.add(identifier)
        business, source = _status(raw)
        pending = raw.get("pending_external_api") is True
        problems = _problems(raw)
        suite = _suite(raw)
        normalized.append({"id": identifier, "suite": suite, "business_passed": business,
                          "pending_external_api": pending, "status_source": source,
                          "problems": problems})

    business_passed = sum(item["business_passed"] for item in normalized)
    pending = sum(item["pending_external_api"] for item in normalized)
    overall = sum(item["business_passed"] and not item["pending_external_api"] for item in normalized)
    failures: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for item in normalized:
        if item["status_source"] is None:
            reasons = ["missing_business_status"]
        elif not item["business_passed"]:
            reasons = item["problems"] or ["business_failed"]
        else:
            reasons = []
        if item["pending_external_api"]:
            reasons.append("pending_external_api")
        for reason in reasons:
            failures.append({"id": item["id"], "suite": item["suite"], "reason": reason})
            reason_counts[reason] += 1

    by_suite: dict[str, dict[str, Any]] = OrderedDict()
    for item in normalized:
        bucket = by_suite.setdefault(item["suite"], {"total": 0, "business_passed": 0,
                                                        "pending_external_api": 0, "overall": 0,
                                                        "failures": []})
        bucket["total"] += 1
        bucket["business_passed"] += int(item["business_passed"])
        bucket["pending_external_api"] += int(item["pending_external_api"])
        bucket["overall"] += int(item["business_passed"] and not item["pending_external_api"])
    for failure in failures:
        by_suite[failure["suite"]]["failures"].append({"id": failure["id"], "reason": failure["reason"]})
    for bucket in by_suite.values():
        bucket["pass_rate"] = round(bucket["overall"] / bucket["total"], 4) if bucket["total"] else 0

    return {
        "overall": overall,
        "total": total,
        "business_passed": business_passed,
        "pending_external_api": pending,
        "pass_rate": round(overall / total, 4) if total else 0,
        "by_suite": by_suite,
        "failures": failures,
        "failure_reasons": dict(reason_counts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a four-task evaluation report")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        scored = score_report(report)
    except (OSError, json.JSONDecodeError, ScorecardError) as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(scored, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
