"""Agent 评测机：按 eval/agent 题库逐题跑通 B 的问答链路并比对标准答案。

用法：
    python scripts/run_agent_eval.py --files eval_basic_nl2sql --mode live
    python scripts/run_agent_eval.py --all --mode live --out tmp/eval-report.json

判定分三层，逐层给出失败原因，便于定位是解析、SQL 还是答复合成的问题：
    1. 接口层：status / route / 必填非空字段
    2. 数据层：gold_result 与实际 SQL 结果行列比对（含数值容差）
    3. 证据层：expected_documents 与检索到的文档、gold_calculation 与计算结果
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVAL_DIR = ROOT / "eval" / "agent"


def load_environment():
    """uv run 会自动读取 .env；直接用解释器跑评测机时在这里补上，避免静默退回离线配置。"""
    if os.environ.get("LLM_API_KEY"):
        return
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
        except ImportError:
            return
        load_dotenv(env_file, override=False)


load_environment()
TOLERANCE = 0.01

# 评测集用 customer_count 表示「筛选范围内的购买客户数」；C 目录里 customer_count 是
# Customer 表记录数，purchasing_customers 才是购买客户数。两处命名冲突在报告中显式记录。
KEY_ALIASES = {"customer_count": ("customer_count", "purchasing_customers")}

MISSING = object()


def load_cases(names):
    cases = []
    for name in names:
        path = EVAL_DIR / f"{name}.jsonl"
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _is_number(text):
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def numeric_equal(want, got):
    try:
        return abs(float(want) - float(got)) <= TOLERANCE
    except (TypeError, ValueError):
        return False


def row_problems(gold_row, actual_row):
    problems = []
    for key, want in gold_row.items():
        names = KEY_ALIASES.get(key, (key,))
        got = next((actual_row[n] for n in names if n in actual_row), MISSING)
        if got is MISSING:
            problems.append(f"缺少列 {key}")
        elif isinstance(want, bool) or isinstance(got, bool):
            if bool(want) is not bool(got):
                problems.append(f"{key}: 期望 {want!r}，实际 {got!r}")
        elif isinstance(want, (int, float)):
            if not numeric_equal(want, got):
                problems.append(f"{key}: 期望 {want}，实际 {got}")
        elif isinstance(want, str) and _is_number(want):
            if not numeric_equal(want, got):
                problems.append(f"{key}: 期望 {want}，实际 {got}")
        elif str(want) != str(got):
            problems.append(f"{key}: 期望 {want!r}，实际 {got!r}")
    return problems


def success_rows(response):
    rows = []
    for result in response.sql_results:
        if result.status == "success":
            rows.extend(result.rows)
    return rows


def compare_gold_result(case, response):
    gold = case.get("gold_result")
    expectation = case.get("interface_expectation", {})
    if gold is None or expectation.get("expected_status") != "answered":
        return []
    expected = gold if isinstance(gold, list) else [gold]
    rows = success_rows(response)
    if response.route == "sql":
        if len(rows) != len(expected):
            return [f"结果行数 {len(rows)}，期望 {len(expected)}"]
        problems = []
        for index, (want, got) in enumerate(zip(expected, rows), 1):
            problems.extend(f"第{index}行 {item}" for item in row_problems(want, got))
        if problems:
            unordered = []
            for want in expected:
                match = next((got for got in rows if not row_problems(want, got)), None)
                if match is None:
                    unordered.append(json.dumps(want, ensure_ascii=False))
            if not unordered:
                return ["行内容正确但顺序与标准答案不一致"]
        return problems
    # 跨源/计算类问题：标准结果是计算产物，只要数值能在答案或计算里找到即可。
    haystack = json.dumps([response.answer, [c.result for c in response.calculations], rows], ensure_ascii=False)
    missing = []
    for want in expected:
        for key, value in want.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not any(numeric_equal(value, item) for item in _numbers(haystack)):
                    missing.append(f"{key}={value}")
            elif isinstance(value, str) and value.strip() and value.strip() not in haystack:
                missing.append(f"{key}={value}")
    return [f"答案未体现计算结果：{'、'.join(missing)}"] if missing else []


def _numbers(text):
    import re
    return [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", text)]


def compare_calculation(case, response):
    gold = case.get("gold_calculation")
    expectation = case.get("interface_expectation", {})
    if not gold or expectation.get("expected_status") != "answered":
        return []
    haystack = json.dumps([response.answer, [c.result for c in response.calculations]], ensure_ascii=False)
    missing = []
    for key, value in gold.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not any(numeric_equal(value, item) for item in _numbers(haystack)):
                missing.append(f"{key}={value}")
        elif isinstance(value, str):
            digits = _numbers(value)
            if digits and not any(numeric_equal(digits[0], item) for item in _numbers(haystack)):
                missing.append(f"{key}={value}")
    return [f"计算结果缺失：{'、'.join(missing)}"] if missing else []


def compare_documents(case, response):
    expected = case.get("expected_documents") or []
    expectation = case.get("interface_expectation", {})
    if not expected or expectation.get("expected_status") != "answered":
        return []
    actual = {chunk.doc_id for chunk in response.documents}
    missing = [doc for doc in expected if doc not in actual]
    return [f"未检索到标准证据文档：{'、'.join(missing)}（实际 {sorted(actual) or '无'}）"] if missing else []


def compare_interface(case, response):
    """按题库期望判定接口层结果。

    期望澄清和期望拒答的题目，clarification 与 error 正是正确行为，
    不能当成失败；只有与期望状态不符时才记为问题。
    """
    expectation = case.get("interface_expectation", {})
    want_status = expectation.get("expected_status")
    problems = []
    for key, field in (("expected_status", "status"), ("expected_route", "route")):
        want = expectation.get(key)
        if want is None:
            continue
        got = getattr(response, field)
        got = getattr(got, "value", got)
        if str(got) != str(want):
            problems.append(f"{field}: 期望 {want}，实际 {got}")
    for field in expectation.get("required_nonempty_fields", []):
        value = getattr(response, field, None)
        value = getattr(value, "value", value)
        if value in (None, "", [], {}):
            problems.append(f"必填字段 {field} 为空")

    if want_status == "clarification_required":
        if not response.clarification:
            problems.append("期望要求澄清，实际未返回 clarification")
        else:
            want_slots = set((expectation.get("expected_clarification") or {}).get("missing_slots") or [])
            got_slots = {slot.name for slot in response.clarification.missing_slots}
            if want_slots and want_slots != got_slots:
                problems.append(f"澄清槽位：期望 {sorted(want_slots)}，实际 {sorted(got_slots)}")
    elif response.clarification:
        slots = [slot.name for slot in response.clarification.missing_slots]
        problems.append(f"意外要求澄清 {slots}：{response.clarification.question}")

    if want_status in ("unsupported", "error"):
        if not response.limitations:
            problems.append("期望拒答时未返回任何限制说明")
    elif response.error:
        problems.append(f"返回错误 {response.error.code}: {response.error.message}")
    return problems


def judge(case, response):
    problems = compare_interface(case, response)
    problems += compare_gold_result(case, response)
    problems += compare_calculation(case, response)
    problems += compare_documents(case, response)
    return problems


async def run_single(service, case, mode):
    expectation = case.get("interface_expectation", {})
    request_body = expectation.get("request", {})
    from services.agent_api.app.contracts import AskRequest, AskOptions

    options = request_body.get("options") or {}
    started = time.monotonic()
    try:
        response = await service.ask(
            AskRequest(
                question=case["question"],
                profile_id=request_body.get("profile_id", "chinook-music"),
                session_id=request_body.get("session_id"),
                user_role=request_body.get("user_role", "operator"),
                options=AskOptions(**options) if options else AskOptions(),
            ),
            request_id=f"eval-{case['id']}",
        )
    except Exception as exc:  # noqa: BLE001 - 评测机需要记录任何异常
        return {"id": case["id"], "question": case["question"], "passed": False,
                "problems": [f"调用异常 {type(exc).__name__}: {exc}"], "elapsed_ms": 0}
    elapsed = round((time.monotonic() - started) * 1000)
    problems = judge(case, response)
    return {
        "id": case["id"], "question": case["question"], "capability": case.get("capability"),
        "passed": not problems, "problems": problems, "elapsed_ms": elapsed,
        "status": str(response.status), "route": str(getattr(response.route, "value", response.route)),
        "answer": (response.answer or "")[:300],
        "rows": success_rows(response)[:6],
        "documents": sorted({chunk.doc_id for chunk in response.documents}),
        "limitations": list(response.limitations)[:6],
    }


async def run_session(service, case):
    from services.agent_api.app.contracts import AskRequest, AskOptions

    turns = case.get("turns", [])
    first_body = (turns[0].get("interface_expectation", {}) if turns else {})
    session_id = (first_body.get("request") or {}).get("session_id") or f"eval-{case['id']}"
    problems, details, started = [], [], time.monotonic()
    for turn in case.get("turns", []):
        body = turn.get("interface_expectation", {})
        options = (body.get("request") or {}).get("options") or {}
        try:
            response = await service.ask(
                AskRequest(question=turn["question"], profile_id=body.get("profile_id", "chinook-music"),
                           session_id=session_id, user_role="operator",
                           options=AskOptions(**options) if options else AskOptions()),
                request_id=f"eval-{case['id']}-t{turn['turn']}",
            )
        except Exception as exc:  # noqa: BLE001
            problems.append(f"第{turn['turn']}轮调用异常 {type(exc).__name__}: {exc}")
            break
        turn_problems = []
        want_status, want_route = body.get("expected_status"), body.get("expected_route")
        got_route = str(getattr(response.route, "value", response.route))
        if want_status and str(response.status) != want_status:
            turn_problems.append(f"status 期望 {want_status}，实际 {response.status}")
        if want_route and got_route != want_route:
            turn_problems.append(f"route 期望 {want_route}，实际 {got_route}")
        if response.clarification:
            turn_problems.append(f"要求澄清 {[s.name for s in response.clarification.missing_slots]}")
        expected_docs = turn.get("expected_documents") or []
        actual_docs = {chunk.doc_id for chunk in response.documents}
        missing_docs = [doc for doc in expected_docs if doc not in actual_docs]
        if missing_docs:
            turn_problems.append(f"未检索到 {'、'.join(missing_docs)}")
        gold_numbers = _numbers(turn.get("gold_answer") or "")
        if gold_numbers:
            haystack = json.dumps([response.answer, [c.result for c in response.calculations],
                                   success_rows(response)], ensure_ascii=False)
            actual_numbers = _numbers(haystack)
            absent = [n for n in gold_numbers if not any(numeric_equal(n, a) for a in actual_numbers)]
            if absent:
                turn_problems.append(f"标准答案数值未体现：{absent}")
        problems.extend(f"第{turn['turn']}轮 {item}" for item in turn_problems)
        details.append({"turn": turn["turn"], "question": turn["question"], "status": str(response.status),
                        "route": got_route, "problems": turn_problems})
    return {"id": case["id"], "question": f"{len(case.get('turns', []))} 轮会话", "capability": "multiturn",
            "passed": not problems, "problems": problems, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "turns": details}


async def main():
    parser = argparse.ArgumentParser(description="运行 Agent 评测集")
    parser.add_argument("--files", help="逗号分隔的题库文件名（不含 .jsonl）")
    parser.add_argument("--all", action="store_true", help="运行 manifest 里的全部题库")
    parser.add_argument("--mode", default="live", choices=["live", "offline"], help="模型模式")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="每个题库最多跑几题，0 表示不限")
    parser.add_argument("--out", default="", help="JSON 报告输出路径")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    manifest = json.load(io.open(EVAL_DIR / "eval_manifest.json", encoding="utf-8"))
    names = list(manifest["files"]) if args.all else (args.files or "eval_basic_nl2sql").split(",")
    names = [n.strip() for n in names if n.strip()]
    # manifest 的文件名带 .jsonl 后缀，--files 允许省略，这里统一去掉。
    names = [n[: -len(".jsonl")] if n.endswith(".jsonl") else n for n in names]

    from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration

    boot = time.monotonic()
    integration = await create_canonical_knowledge_integration(model_mode=args.mode)
    service = integration.create_formal_graph_service()
    boot_ms = round((time.monotonic() - boot) * 1000)
    if not args.quiet:
        print(f"模式={args.mode} 索引与后端启动耗时={boot_ms}ms")

    results, sessions = [], []
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    try:
        for name in names:
            cases = load_cases([name])
            if args.limit:
                cases = cases[: args.limit]
            single = [c for c in cases if "question" in c]
            multi = [c for c in cases if "turns" in c]

            async def guarded(case):
                async with semaphore:
                    return await run_single(service, case, args.mode)

            batch = await asyncio.gather(*(guarded(c) for c in single))
            results.extend(batch)
            for case in multi:
                try:
                    sessions.append(await run_session(service, case))
                except Exception as exc:  # noqa: BLE001 - 单题异常不阻断整轮评测
                    sessions.append({"id": case["id"], "question": "多轮会话", "capability": "multiturn",
                                     "passed": False, "problems": [f"会话异常 {type(exc).__name__}: {exc}"],
                                     "elapsed_ms": 0})
            if not args.quiet:
                passed = sum(1 for item in batch if item["passed"])
                print(f"  {name}: {passed}/{len(batch)} 通过")
    finally:
        await integration.aclose(grace_seconds=5)

    all_results = results + sessions
    passed = sum(1 for item in all_results if item["passed"])
    elapsed = [item["elapsed_ms"] for item in all_results if item.get("elapsed_ms")]
    report = {
        "mode": args.mode, "files": names, "total": len(all_results), "passed": passed,
        "pass_rate": round(passed / len(all_results), 4) if all_results else 0,
        "boot_ms": boot_ms,
        "latency_ms": {"avg": round(sum(elapsed) / len(elapsed)) if elapsed else 0,
                       "min": min(elapsed) if elapsed else 0, "max": max(elapsed) if elapsed else 0},
        "known_metric_naming_conflict": "评测集 customer_count 表示购买客户数，等价于 C 目录的 purchasing_customers",
        "results": all_results,
    }
    out = args.out or str(ROOT / "tmp" / f"eval-report-{args.mode}-{int(time.time())}.json")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    io.open(out, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"\n总计 {passed}/{len(all_results)} 通过（{report['pass_rate'] * 100:.1f}%）"
          f"，平均 {report['latency_ms']['avg']}ms，最慢 {report['latency_ms']['max']}ms")
    failures = [item for item in all_results if not item["passed"]]
    if failures:
        print("\n未通过明细：")
        for item in failures:
            print(f"  [{item['id']}] {item['question']}")
            for problem in item["problems"]:
                print(f"      - {problem}")
    print(f"\n报告已写入 {out}")
    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
