"""Read-only diagnostics over frozen responses; never supplied to the Agent.

Does not replace the original grader, add corrected scores, or mutate gold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def normalized_slots(names):
    """Normalize only declared equivalents; preserve entity vs dimension."""
    result = set(names)
    if {"year", "quarter"} <= result:
        result.difference_update({"year", "quarter"})
        result.add("period")
    if "genre_boundary" in result:
        result.remove("genre_boundary")
        result.add("genre_scope")
    return sorted(result)


def audit_response(record):
    response, question = record["response"], record["request"]["question"]
    findings = []
    if response.get("status") == "clarification_required":
        names = [s["name"] for s in (response.get("clarification") or {}).get("missing_slots", [])]
        findings.append({"kind": "clarification", "slots": normalized_slots(names)})
    if response.get("status") == "answered" and response.get("route") == "rag":
        text = response.get("answer") or ""
        tier_labels = all(re.search(r"(?:^|\n)" + tier + r"\s*(?:层|\|)", text) or re.search(tier + r"\s*层", text) for tier in "ABC")
        tier_units = any(w in text for w in ("美元", "USD")) and bool(re.search(r"大于等于\s*\d+(?:\.\d+)?", text)) and bool(re.search(r"\d+(?:\.\d+)?\s*至\s*\d+(?:\.\d+)?\s*以下", text))
        if "分层" in question and not (tier_labels and tier_units):
            findings.append({"kind": "fact_gap", "fact": "tier_thresholds"})
        if any(x in question for x in ("报名要交", "提交材料")) and not all(x in text for x in ("商品清单", "选品理由", "文案")):
            findings.append({"kind": "fact_gap", "fact": "submission_materials"})
        if "活动什么时候举行" in question and not re.search(r"\d+月\d+日\s*[－—至-]\s*(?:\d{4}年)?\d+月\d+日", text):
            findings.append({"kind": "fact_gap", "fact": "activity_period"})
    if response.get("error"):
        findings.append({"kind": "execution_error", "error": response["error"]})
    return {"request_id": record["request_id"], "status": response.get("status"), "findings": findings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    target = Path(args.out)
    if target.exists():
        raise SystemExit("Refusing to overwrite diagnostics")
    records = [json.loads(line) for line in Path(args.responses).read_text(encoding="utf-8").splitlines() if line.strip()]
    data = {"kind": "diagnostics_not_rescoring", "requests": len(records), "results": [audit_response(r) for r in records]}
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"diagnostic requests={len(records)}; no corrected score")


if __name__ == "__main__":
    main()
