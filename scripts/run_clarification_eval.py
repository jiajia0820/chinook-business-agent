"""Original eight + independent reply diagnostics, no gold enters the Agent."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_agent_eval as grader
from scripts.run_c_regression import CapturingService, digest
from backend.sql_module.generation import HTTPJSONModel, decode_generated
from services.agent_api.app.agent.session import session_key
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


def scalar_values(spec):
    """Independent test oracle over read-only DB; not available to runtime."""
    year = spec["year"]
    month = (spec["quarter"] - 1) * 3 + 1 if spec.get("quarter") else 1
    end_month = month + 3 if spec.get("quarter") else 13
    start, end = f"{year}-{month:02d}-01", f"{year + (end_month > 12)}-{1 if end_month > 12 else end_month:02d}-01"
    expressions = {"sales_amount": "ROUND(COALESCE(SUM(il.UnitPrice*il.Quantity),0),2)", "units_sold": "COALESCE(SUM(il.Quantity),0)"}
    metrics = spec["metrics"]
    sql = "SELECT " + ",".join(expressions[m] for m in metrics)
    sql += " FROM InvoiceLine il JOIN Invoice i ON i.InvoiceId=il.InvoiceId JOIN Track t ON t.TrackId=il.TrackId WHERE i.InvoiceDate>=? AND i.InvoiceDate<?"
    params = [start, end]
    for column, values in (("MediaTypeId", spec["media"]), ("GenreId", spec.get("genres", []))):
        if values:
            sql += " AND t." + column + " IN (" + ",".join("?" for _ in values) + ")"
            params.extend(values)
    with sqlite3.connect((ROOT / "data/chinook/Chinook.db").as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(sql, params).fetchone()
    return dict(zip(metrics, row))


def judge(expected, response, parsed, events):
    issues, task = [], parsed.get("task") or {}
    slots = task.get("slots", {})
    if response.status != expected["status"]:
        issues.append("status:" + response.status + "!=" + expected["status"])
    if "missing" in expected:
        actual = {s.name for s in response.clarification.missing_slots} if response.clarification else set()
        if actual != set(expected["missing"]):
            issues.append("missing_slots:" + str(sorted(actual)))
    if "blocking" in expected and set(parsed.get("blocking_slots", [])) != set(expected["blocking"]):
        issues.append("deferred_blockers")
    if expected.get("no_tools") and (response.sql_results or response.documents or response.calculations or events):
        issues.append("tool_before_confirmation")
    if "intent" in expected and task.get("intent") != expected["intent"]:
        issues.append("intent")
    if "metrics" in expected and set(task.get("business_metric_ids", [])) != set(expected["metrics"]):
        issues.append("metric_binding")
    if "media" in expected and slots.get("media_ids") != expected["media"]:
        issues.append("media_binding")
    if "genres" in expected:
        actual = sorted(int(e["id"].split(":")[1]) for e in task.get("entities", []) if e["type"] == "genre")
        if actual != expected["genres"]:
            issues.append("genre_binding")
    for name, value in expected.get("slot_values", {}).items():
        if slots.get(name) != value:
            issues.append("slot:" + name)
    if "period_start" in expected and (not response.time_range or response.time_range.start != expected["period_start"]):
        issues.append("period_start")
    for term in expected.get("contains", []):
        if term not in (response.answer or "") and term not in (response.clarification.question if response.clarification else ""):
            issues.append("missing_explanation:" + term)
    if len(response.calculations) < expected.get("min_calculations", 0):
        issues.append("calculation_completion")
    if expected.get("union_plan"):
        if not response.sql_results or "GenreId IN (:genre_id_0,:genre_id_1)" not in response.sql_results[0].sql:
            issues.append("union_set_plan")
    if "scalar" in expected:
        values = scalar_values(expected["scalar"])
        rows = [row for result in response.sql_results if result.status == "success" for row in result.rows]
        for metric, value in values.items():
            if not any(metric in row and abs(float(row[metric]) - float(value)) <= 0.01 for row in rows):
                issues.append("scalar:" + metric + "=" + str(value))
    return issues


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--mode", choices=("live", "stub"), default="live")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Refusing to overwrite existing diagnostics")
    out.mkdir(parents=True)
    fixture = ROOT / "eval/clarification_v3/dialogues.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    protected = [ROOT / "scripts/run_agent_eval.py", ROOT / "eval/agent/eval_manifest.json", ROOT / "data/chinook/Chinook.db"]
    protected += sorted((ROOT / "eval/agent").glob("*.jsonl"))
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    calls, original = [], HTTPJSONModel.generate_json

    def observe(self, messages, schema, deadline):
        started = time.monotonic()
        try:
            value = original(self, messages, schema, deadline)
            try:
                decode_generated(value)
                valid = True
            except Exception:
                valid = False
            calls.append({"source": "real_model", "status": "returned", "valid_candidate_json": valid, "elapsed_ms": round((time.monotonic() - started) * 1000)})
            return value
        except Exception as exc:
            calls.append({"source": "real_model", "status": "failed", "reason": getattr(exc, "reason", None), "elapsed_ms": round((time.monotonic() - started) * 1000)})
            raise

    class StubCandidate:
        def generate_json(self, messages, schema, deadline):
            calls.append({"source": "artificial_fixture", "status": "returned", "valid_candidate_json": True, "elapsed_ms": 0})
            return json.dumps({"sql": "SELECT COUNT(*) AS customer_count FROM main.Customer", "params": {}})

    started = time.monotonic()
    if args.mode == "live":
        HTTPJSONModel.generate_json = observe
        integration = await create_canonical_knowledge_integration(model_mode="live", sql_timeout_ms=180000)
    else:
        with patch.object(HTTPJSONModel, "from_env", return_value=StubCandidate()):
            integration = await create_canonical_knowledge_integration(model_mode="live", sql_timeout_ms=180000)
    service = CapturingService(integration.create_formal_graph_service())
    results, original_results, originals_count = [], [], len(calls)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    def persist():
        (out / "responses.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in service.responses) + "\n", encoding="utf-8")
        (out / "partial-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    async def run(case):
        async with semaphore:
            turns, prior_id = [], None
            session = "clar-v3-" + case["id"]
            for index, expected in enumerate(case["turns"]):
                response = await service.ask(AskRequest(question=expected["question"], profile_id="chinook-music", session_id=session), request_id=f"clar-v3-{case['id']}-{index}")
                state = await service.service.graph.aget_state({"configurable": {"thread_id": session_key("chinook-music", session)}})
                parsed = state.values.get("parsed", {})
                task = parsed.get("task") or {}
                issues = judge(expected, response, parsed, state.values.get("events", []))
                if prior_id and task and task["task_id"] != prior_id:
                    issues.append("pending_task_identity_lost")
                if expected.get("reset"):
                    prior_id = None
                    if state.values.get("memory", {}).get("pending") or state.values.get("memory", {}).get("last_task"):
                        issues.append("cancel_not_cleared")
                elif task:
                    prior_id = task["task_id"]
                turns.append({"question": expected["question"], "status": response.status, "passed": not issues,
                    "issues": issues, "parsed": parsed, "scalar_expected": scalar_values(expected["scalar"]) if "scalar" in expected else None})
            result = {"id": case["id"], "passed": all(t["passed"] for t in turns), "turns": turns}
            results.append(result)
            print(case["id"] + ": " + ("PASS" if result["passed"] else "FAIL " + str([t["issues"] for t in turns if t["issues"]])), flush=True)
            persist()

    try:
        for case in grader.load_cases(["eval_special_clarification"]):
            original_results.append(await grader.run_single(service, case, args.mode))
        originals_count = len(calls) - originals_count
        print(f"ORIGINAL EIGHT: {sum(r['passed'] for r in original_results)}/8; model_calls={originals_count}", flush=True)
        await asyncio.gather(*(run(case) for case in cases))
    finally:
        await integration.aclose(grace_seconds=10)
        HTTPJSONModel.generate_json = original
    after = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    report = {"kind": "development_diagnostics_not_holdout", "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit, "working_tree_dirty_at_start": dirty, "mode": args.mode, "real_model_used": args.mode == "live",
        "model": os.getenv("LLM_MODEL") if args.mode == "live" else "artificial_fixture_not_model",
        "provider_host": urlsplit(os.getenv("LLM_BASE_URL", "")).hostname if args.mode == "live" else None,
        "fixture_sha256": digest(fixture), "protected_sha256_before": before, "protected_sha256_after": after,
        "protected_unchanged": before == after, "original_clarification": {"passed": sum(r["passed"] for r in original_results), "total": len(original_results), "model_calls": originals_count, "results": original_results},
        "dialogues": {"passed": sum(r["passed"] for r in results), "total": len(results), "turns_passed": sum(t["passed"] for r in results for t in r["turns"]), "turns": sum(len(r["turns"]) for r in results), "results": results},
        "turn_statuses": dict(Counter(t["status"] for r in results for t in r["turns"])), "model_calls": calls, "wall_seconds": round(time.monotonic() - started, 2)}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 主动澄清专项诊断", "", f"模式：{args.mode}；提交：{commit}；耗时：{report['wall_seconds']}秒。", "",
        f"原8题：{report['original_clarification']['passed']}/8（原评分器未改）；开发对话：{report['dialogues']['passed']}/{len(results)}；逐轮：{report['dialogues']['turns_passed']}/{report['dialogues']['turns']}。", "",
        "这不是独立盲测。原8题本身无需模型调用；只有补齐执行条件的对话才调用模型。unsupported计为边界行为正确，不计为成功业务回答。", "",
        "| 对话 | 结果 | 逐轮状态 | 问题 |", "| --- | --- | --- | --- |"]
    lines += [f"| {r['id']} | {'通过' if r['passed'] else '未通过'} | {' → '.join(t['status'] for t in r['turns'])} | {'；'.join(i for t in r['turns'] for i in t['issues'])} |" for r in results]
    lines += ["", "## 原8题剩余问题", ""]
    lines += [r["id"] + "：" + "；".join(r["problems"]) for r in original_results if not r["passed"]]
    (out / "专项诊断结果.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"TOTAL {report['dialogues']['passed']}/{len(results)}; turns={report['dialogues']['turns_passed']}/{report['dialogues']['turns']}; output={out}")
    return 0 if report["protected_unchanged"] and report["dialogues"]["passed"] == len(results) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
