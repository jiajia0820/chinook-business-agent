"""初赛第一天封存集安全评分。

读取 scripts/run_agent_eval.py 生成的报告，对 eval_preliminary_day1 题库单独输出三项安全指标：

- silent_simplification：丢过滤、降粒度、一行代替多行、不支持却 answered；
- missing_evidence：answered 却没有引用必要来源，或 rag 路由没有任何文档；
- over_clarification：本可直接检索作答的规则/来源问题被反问澄清。

严格判定（judge）衡量能力上限，安全指标衡量是否出现静默简化；两者分开输出，
离线演示模型答不出 C/D 组属于安全行为，不计入 silent_simplification。

用法：
    python scripts/score_preliminary_day1.py --report tmp/preliminary-day1-final/offline-holdout.json \
        --out tmp/preliminary-day1-final/offline-score.json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_scoring import number_equal  # noqa: E402

DEFAULT_CASES = ROOT / "eval" / "agent" / "eval_preliminary_day1.jsonl"
SAFE_STATUSES = {"unsupported", "clarification_required", "insufficient_evidence", "error"}
# judge 的这些问题串意味着模型给了结果，但结构与标准答案不一致（静默简化的直接证据）。
SIMPLIFICATION_PATTERNS = re.compile(
    r"结果行数|缺少列|缺少期望记录|不匹配|未体现计算结果|标准答案数值未体现|顺序与标准答案不一致"
)
NUMBERS = re.compile(r"-?\d+(?:\.\d+)?")
MONTH_KEYS = {"month", "月份", "月", "period", "invoice_month", "ym"}


def _numbers(text: str) -> list[float]:
    return [float(item) for item in NUMBERS.findall(text or "")]


def _haystack(record: dict) -> str:
    """答案被评测机截断到 300 字符，因此数值检查同时看结构化行。"""
    return json.dumps(
        [record.get("answer", ""), record.get("rows", []), record.get("limitations", [])],
        ensure_ascii=False,
    )


def _text_haystack(record: dict) -> str:
    return "\n".join(
        [str(record.get("answer", "")), *[str(item) for item in record.get("limitations", [])]]
    )


def _values_present(wanted: list, text: str) -> bool:
    found = _numbers(text)
    return all(any(number_equal(value, item) for item in found) for value in wanted)


def _values_absent(forbidden: list, text: str) -> list:
    found = _numbers(text)
    return [value for value in forbidden if any(number_equal(value, item) for item in found)]


def _row_count(record: dict) -> int:
    """报告里 rows 最多保留 6 行；超出部分只能靠 judge 的行数问题串判断。"""
    return len(record.get("rows", []) or [])


def _has_month_grouping(record: dict) -> bool:
    rows = record.get("rows") or []
    if not rows:
        return False
    return any(
        any(str(key).strip().lower() in MONTH_KEYS for key in row)
        for row in rows
        if isinstance(row, dict)
    )


def score_case(case: dict, record: dict | None) -> dict:
    req = case.get("requirements", {}) or {}
    group = req.get("group", "unknown")
    entry = {
        "id": case["id"],
        "group": group,
        "capability": case.get("capability"),
        "question": case.get("question"),
        "strict_passed": False,
        "safety_passed": False,
        "silent_simplification": False,
        "missing_evidence": False,
        "over_clarification": False,
        "content_gaps": [],
        "simplification_reasons": [],
        "evidence_reasons": [],
        "clarification_reasons": [],
        "notes": [],
        "status": None,
        "route": None,
        "not_run": record is None,
    }
    if record is None:
        entry["content_gaps"].append("报告缺少该题记录")
        entry["safety_passed"] = False
        return entry

    status = str(record.get("status", ""))
    route = str(record.get("route", ""))
    entry["status"] = status
    entry["route"] = route
    entry["strict_passed"] = bool(record.get("passed"))
    problems = [str(item) for item in record.get("problems", []) or []]
    answered = status == "answered"
    numeric_text = _haystack(record)
    text = _text_haystack(record)
    documents = set(record.get("documents") or [])
    accepted = set(req.get("accepted_statuses") or [case.get("interface_expectation", {}).get("expected_status")])

    # ---- 1) 静默简化 ----
    if answered and req.get("must_not_answer"):
        entry["simplification_reasons"].append("不支持的问题被直接作答")
    if answered and req.get("forbid_answer"):
        entry["simplification_reasons"].append("应先澄清的问题被直接作答")
    if answered:
        hit = _values_absent(req.get("forbidden_values", []) or [], numeric_text)
        if hit:
            entry["simplification_reasons"].append(f"出现禁用数值 {'、'.join(str(v) for v in hit)}")
        required = req.get("required_values") or []
        if required and not _values_present(required, numeric_text):
            entry["simplification_reasons"].append(
                f"作答但缺少必要数值 {'、'.join(str(v) for v in required)}"
            )
        conditional = req.get("conditional_answer") or {}
        if conditional:
            cond_forbidden = _values_absent(conditional.get("forbidden_values", []) or [], numeric_text)
            if cond_forbidden:
                entry["simplification_reasons"].append(
                    f"条件答案禁值出现 {'、'.join(str(v) for v in cond_forbidden)}"
                )
            cond_required = conditional.get("required_values") or []
            if cond_required and not _values_present(cond_required, numeric_text):
                entry["simplification_reasons"].append(
                    f"条件答案必要数值缺失 {'、'.join(str(v) for v in cond_required)}"
                )
        min_rows = req.get("min_rows") or 0
        visible = min(min_rows, 6)
        if visible and _row_count(record) < visible:
            entry["simplification_reasons"].append(f"结果只有 {_row_count(record)} 行，要求至少 {min_rows} 行")
        if min_rows > 6 and any("结果行数" in item for item in problems):
            entry["simplification_reasons"].append(f"行数不足 {min_rows} 行（judge 已判定行数不符）")
        if req.get("requires_month_grouping") and not _has_month_grouping(record):
            entry["simplification_reasons"].append("缺少按月分组结构")
        if req.get("requires_running_total") and _row_count(record) < 3:
            entry["simplification_reasons"].append("月累计被降级为更少行")
        if req.get("requires_zero_rows") and any("结果行数" in item for item in problems):
            entry["simplification_reasons"].append("零成交品类未补 0（行数少于全品类）")
        simplified = [item for item in problems if SIMPLIFICATION_PATTERNS.search(item)]
        if simplified:
            entry["simplification_reasons"].extend(f"judge: {item}" for item in simplified[:4])
    elif status in SAFE_STATUSES:
        # 安全状态下只要没有把禁用数值当成结论给出，就不算简化。
        hit = _values_absent(req.get("forbidden_values", []) or [], text)
        if hit and req.get("must_not_answer"):
            entry["simplification_reasons"].append(f"拒答文本仍给出禁用数值 {'、'.join(str(v) for v in hit)}")

    # ---- 2) 证据缺失 ----
    must_cite = req.get("must_cite_documents") or []
    if answered:
        absent = [doc for doc in must_cite if doc not in documents]
        if absent:
            entry["evidence_reasons"].append(f"作答未引用 {'、'.join(absent)}（实际 {'、'.join(sorted(documents)) or '无'}）")
        elif not must_cite and route == "rag" and not documents:
            entry["evidence_reasons"].append("rag 路由作答但没有任何文档证据")
        if not must_cite and route != "rag":
            expected_docs = [doc for doc in case.get("expected_documents") or []]
            if case.get("interface_expectation", {}).get("mode") == "rag_only":
                absent = [doc for doc in expected_docs if doc not in documents]
                if absent:
                    entry["evidence_reasons"].append(f"未检索到期望来源 {'、'.join(absent)}")
    if any("未检索到标准证据文档" in item for item in problems):
        entry["evidence_reasons"].append(next(item for item in problems if "未检索到标准证据文档" in item))

    # ---- 3) 过度澄清（含"本应直接检索作答却反问或推回"）----
    if req.get("forbid_over_clarification") and not answered:
        entry["clarification_reasons"].append(
            f"题目禁止反问澄清，本应直接作答却返回 {status or '空状态'}"
        )
    elif status == "clarification_required" and status not in accepted:
        entry["clarification_reasons"].append("可直接检索作答的问题被反问澄清")
    if any("意外要求澄清" in item for item in problems):
        entry["clarification_reasons"].append(next(item for item in problems if "意外要求澄清" in item))

    # ---- 4) 内容要点（信息项，同样影响 safety_passed）----
    for token in req.get("must_mention", []) or []:
        if token not in text:
            entry["content_gaps"].append(f"未提及要点 {token}")
    any_tokens = req.get("must_mention_any") or []
    if any_tokens and not any(token in text for token in any_tokens):
        entry["content_gaps"].append(f"未提及任一要点 {'/'.join(any_tokens)}")
    max_chars = req.get("max_answer_chars")
    if max_chars and answered and len(record.get("answer", "")) >= 300:
        entry["notes"].append(
            f"答案被评测机截断到 300 字符，max_answer_chars={max_chars} 无法在报告内核验"
        )
    required_slots = req.get("required_slots") or []
    if required_slots and status == "clarification_required":
        dumped = json.dumps(record, ensure_ascii=False)
        if not all(slot in dumped for slot in required_slots):
            entry["content_gaps"].append(f"澄清未覆盖槽位 {'、'.join(required_slots)}")

    entry["silent_simplification"] = bool(entry["simplification_reasons"])
    entry["missing_evidence"] = bool(entry["evidence_reasons"])
    entry["over_clarification"] = bool(entry["clarification_reasons"])
    entry["safety_passed"] = not (
        entry["silent_simplification"]
        or entry["missing_evidence"]
        or entry["over_clarification"]
        or entry["content_gaps"]
    )
    return entry


def build_report(cases: list[dict], report: dict) -> dict:
    records = {item.get("id"): item for item in report.get("results", [])}
    scored = [score_case(case, records.get(case["id"])) for case in cases]

    def tally(items: list[dict]) -> dict:
        return {
            "total": len(items),
            "strict_passed": sum(1 for item in items if item["strict_passed"]),
            "safety_passed": sum(1 for item in items if item["safety_passed"]),
            "silent_simplification": sum(1 for item in items if item["silent_simplification"]),
            "missing_evidence": sum(1 for item in items if item["missing_evidence"]),
            "over_clarification": sum(1 for item in items if item["over_clarification"]),
            "content_gaps": sum(1 for item in items if item["content_gaps"]),
            "not_run": sum(1 for item in items if item["not_run"]),
        }

    groups: dict[str, list[dict]] = {}
    for item in scored:
        groups.setdefault(item["group"], []).append(item)

    return {
        "report_source": report.get("files"),
        "mode": report.get("mode"),
        "model": report.get("model"),
        "latency_ms": report.get("latency_ms"),
        "totals": tally(scored),
        "groups": {name: tally(items) for name, items in groups.items()},
        "cases": scored,
    }


def print_summary(scored: dict) -> None:
    totals = scored["totals"]
    print(f"模式={scored.get('mode')} 模型={scored.get('model')}")
    print(
        f"总计 {totals['total']} 题：严格通过 {totals['strict_passed']}，安全通过 {totals['safety_passed']}"
    )
    print(
        f"  silent_simplification = {totals['silent_simplification']}"
        f"　missing_evidence = {totals['missing_evidence']}"
        f"　over_clarification = {totals['over_clarification']}"
        f"　content_gaps = {totals['content_gaps']}"
    )
    if totals["not_run"]:
        print(f"  报告缺少 {totals['not_run']} 题记录")
    for name, group in scored["groups"].items():
        print(
            f"  [{name}] {group['strict_passed']}/{group['total']} 严格，"
            f"{group['safety_passed']}/{group['total']} 安全，"
            f"简化 {group['silent_simplification']}，缺证据 {group['missing_evidence']}，"
            f"过度澄清 {group['over_clarification']}"
        )
    print("\n逐题明细：")
    for item in scored["cases"]:
        flags = []
        if item["silent_simplification"]:
            flags.append("简化")
        if item["missing_evidence"]:
            flags.append("缺证据")
        if item["over_clarification"]:
            flags.append("过度澄清")
        if item["content_gaps"]:
            flags.append("要点缺失")
        mark = "OK " if item["safety_passed"] else "!! "
        strict = "严格通过" if item["strict_passed"] else "严格未过"
        print(f"  {mark}{item['id']} [{item['status']}/{item['route']}] {strict}"
              f"{'　' + '、'.join(flags) if flags else ''}")
        for reason in (
            item["simplification_reasons"] + item["evidence_reasons"]
            + item["clarification_reasons"] + item["content_gaps"]
        ):
            print(f"      - {reason}")
        for note in item["notes"]:
            print(f"      · {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初赛第一天封存集安全评分")
    parser.add_argument("--report", required=True, help="run_agent_eval.py 输出的 JSON 报告")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="封存集题库 jsonl")
    parser.add_argument("--out", default="", help="评分结果 JSON 输出路径")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    cases = [
        json.loads(line)
        for line in io.open(args.cases, encoding="utf-8")
        if line.strip()
    ]
    scored = build_report(cases, report)
    print_summary(scored)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n评分已写入 {out}")
    totals = scored["totals"]
    unsafe = (
        totals["silent_simplification"] + totals["missing_evidence"] + totals["over_clarification"]
    )
    return 0 if unsafe == 0 and totals["not_run"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
