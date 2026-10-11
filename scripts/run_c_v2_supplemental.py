"""Independent policy/fact diagnostics; original 80-case grader is unchanged."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_c_regression import CapturingService, digest
from scripts.run_agent_eval import load_environment
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.knowledge.facts import fact_matches
from backend.sql_module.generation import HTTPJSONModel, decode_generated
import os


def judge(expected, response):
    issues = []
    if response.status != expected["status"]:
        issues.append(f"status:{response.status}!={expected['status']}")
    if "missing" in expected:
        actual = {s.name for s in response.clarification.missing_slots} if response.clarification else set()
        if set(expected["missing"]) != actual:
            issues.append("missing_slots")
    for fact in expected.get("facts", []):
        if not any(fact_matches(fact, c.text) for c in response.documents) or not fact_matches(fact, response.answer or ""):
            issues.append("fact:" + fact)
    if expected.get("no_sql") and response.sql_results:
        issues.append("unexpected_sql")
    if "calculations" in expected and len(response.calculations) != expected["calculations"]:
        issues.append("calculation_count")
    if "sql_results" in expected and len(response.sql_results) != expected["sql_results"]:
        issues.append("sql_result_count")
    if "growth" in expected and (not response.calculations or response.calculations[0].result != expected["growth"]):
        issues.append("growth_value")
    if "track_ids" in expected:
        got = {r.get("TrackId") for result in response.sql_results for r in result.rows}
        if got != set(expected["track_ids"]):
            issues.append("candidate_set")
    if expected.get("customer_scope"):
        rows = [r for result in response.sql_results for r in result.rows]
        if not rows or not all(r.get("sales_amount", -1) >= r.get("selected_genre_amount", 0) > 0 for r in rows):
            issues.append("cohort_vs_all_audio_scope")
    if "period_start" in expected and (not response.time_range or response.time_range.start != expected["period_start"]):
        issues.append("period_start")
    for term in expected.get("contains", []):
        if term not in (response.answer or ""):
            issues.append("missing_fact_value:" + term)
    if len(response.documents) > 5:
        issues.append("whole_question_top_k")
    return issues


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit("Refusing to overwrite existing output")
    out.mkdir(parents=True)
    fixture = ROOT / "eval/c_v2/supplemental.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
    load_environment()
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
            calls.append({"status": "returned", "valid_candidate_json": valid, "elapsed_ms": round((time.monotonic() - started) * 1000)})
            return value
        except Exception as exc:
            calls.append({"status": "failed", "reason": getattr(exc, "reason", None), "elapsed_ms": round((time.monotonic() - started) * 1000)})
            raise
    HTTPJSONModel.generate_json = observe
    started = time.monotonic()
    database = ROOT / "data/chinook/Chinook.db"
    before = digest(database)
    integration = await create_canonical_knowledge_integration(model_mode="live", sql_timeout_ms=180000)
    service = CapturingService(integration.create_formal_graph_service())
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    results = []
    async def run(case):
        async with semaphore:
            turns = []
            for index, expected in enumerate(case.get("turns", [case])):
                response = await service.ask(AskRequest(question=expected["question"], profile_id="chinook-music", session_id="v2-" + case["id"]), request_id=f"v2-{case['id']}-{index}")
                issues = judge(expected, response)
                turns.append({"question": expected["question"], "status": response.status, "passed": not issues, "issues": issues})
            result = {"id": case["id"], "passed": all(t["passed"] for t in turns), "turns": turns}
            results.append(result)
            print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
            (out / "responses.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in service.responses) + "\n", encoding="utf-8")
    try:
        await asyncio.gather(*(run(c) for c in cases))
    finally:
        await integration.aclose(grace_seconds=10)
        HTTPJSONModel.generate_json = original
    report = {"kind": "development_diagnostics_not_original80_or_holdout", "created_at": datetime.now(timezone.utc).isoformat(), "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "model": os.getenv("LLM_MODEL"), "provider_host": urlsplit(os.getenv("LLM_BASE_URL", "")).hostname, "dataset_sha256": digest(fixture), "database_sha256_before": before, "database_sha256_after": digest(database), "database_unchanged": before == digest(database), "total": len(results), "passed": sum(r["passed"] for r in results), "requests": len(service.responses), "model_calls": calls, "wall_seconds": round(time.monotonic() - started, 2), "results": results}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# C二轮补充诊断", "", f"通过 {report['passed']}/{report['total']}；请求 {report['requests']}；模型尝试 {len(calls)}；耗时 {report['wall_seconds']} 秒。", "", "这是开发诊断集，不是原80题重评分或独立盲测。", "", "| 单元 | 结果 | 问题 |", "| --- | --- | --- |"]
    lines += [f"| {r['id']} | {'通过' if r['passed'] else '未通过'} | {'；'.join(i for t in r['turns'] for i in t['issues'])} |" for r in results]
    (out / "补充诊断结果.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"TOTAL {report['passed']}/{report['total']}; output={out}")
    return 0 if report["database_unchanged"] else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
