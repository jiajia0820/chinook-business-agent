"""Full original judge + immutable full responses + baseline comparison.

Runs the existing integration directly; never starts/stops the user's servers.
No gold/expected values enter the Agent; credentials are not logged.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_agent_eval as grader
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from backend.sql_module.generation import HTTPJSONModel, decode_generated


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CapturingService:
    def __init__(self, service):
        self.service, self.responses = service, []

    async def ask(self, payload, *, request_id):
        started = time.monotonic()
        response = await self.service.ask(payload, request_id=request_id)
        self.responses.append({"request_id": request_id, "request": payload.model_dump(mode="json"),
            "response": response.model_dump(mode="json"), "elapsed_ms": round((time.monotonic() - started) * 1000)})
        return response


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if (out / "report.json").exists():
        raise SystemExit("Refusing to overwrite an existing evaluation")
    out.mkdir(parents=True, exist_ok=True)
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    manifest = ROOT / "eval/agent/eval_manifest.json"
    names = list(json.loads(manifest.read_text(encoding="utf-8"))["files"])
    database = ROOT / "data/chinook/Chinook.db"
    started = time.monotonic()
    model_calls = []
    original_generate = HTTPJSONModel.generate_json

    def observed_generate(self, messages, output_schema, deadline):
        clock = time.monotonic()
        try:
            result = original_generate(self, messages, output_schema, deadline)
        except Exception as exc:
            model_calls.append({"status": "failed", "exception_type": type(exc).__name__, "reason": getattr(exc, "reason", None), "elapsed_ms": round((time.monotonic() - clock) * 1000)})
            raise
        try:
            decode_generated(result)
            valid = True
        except Exception:
            valid = False
        model_calls.append({"status": "returned", "valid_candidate_json": valid, "elapsed_ms": round((time.monotonic() - clock) * 1000)})
        return result

    HTTPJSONModel.generate_json = observed_generate
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "mode": "live", "model": grader.os.environ.get("LLM_MODEL"),
        "provider_host": urlsplit(grader.os.environ.get("LLM_BASE_URL", "")).hostname,
        "concurrency": args.concurrency, "grader_sha256": digest(ROOT / "scripts/run_agent_eval.py"),
        "manifest_sha256": digest(manifest), "database_sha256_before": digest(database),
        "dataset_sha256": {name: digest(ROOT / "eval/agent" / name) for name in names},
        "scoring_limitations": baseline.get("scoring_limitations", []), "completed": False}
    (out / "metadata.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    integration = await create_canonical_knowledge_integration(model_mode="live", sql_timeout_ms=180000)
    print(f"Instance ready; startup_seconds={time.monotonic() - started:.2f}", flush=True)
    service = CapturingService(integration.create_formal_graph_service())
    units, suites = [], []
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    try:
        for name in names:
            cases = grader.load_cases([name.removesuffix(".jsonl")])

            async def run(case):
                async with semaphore:
                    result = await grader.run_single(service, case, "live")
                    print(f"  {case['id']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
                    return result

            results = await asyncio.gather(*(run(case) for case in cases if "question" in case))
            for case in cases:
                if "turns" in case:
                    results.append(await grader.run_session(service, case))
            for result in results:
                result["suite"] = name
            units.extend(results)
            suite = {"suite": name, "total": len(results), "passed": sum(r["passed"] for r in results)}
            suites.append(suite)
            print(f"{name}: {suite['passed']}/{suite['total']}", flush=True)
            # Incremental artifacts survive a later interruption.
            (out / "responses.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in service.responses) + "\n", encoding="utf-8")
            (out / "partial-results.json").write_text(json.dumps(units, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await integration.aclose(grace_seconds=10)
        HTTPJSONModel.generate_json = original_generate
    latencies = sorted(item["elapsed_ms"] for item in service.responses)
    report.update(completed=True, total=len(units), passed=sum(r["passed"] for r in units), total_requests=len(service.responses),
        pass_rate=round(sum(r["passed"] for r in units) / len(units), 4), suites=suites, results=units,
        model_attempts=len(model_calls), model_calls=model_calls, wall_seconds=round(time.monotonic() - started, 2),
        request_latency_ms={"mean": round(sum(latencies) / len(latencies)), "p95": latencies[int((len(latencies) - 1) * .95)], "max": max(latencies)},
        database_sha256_after=digest(database))
    report["database_unchanged"] = report["database_sha256_before"] == report["database_sha256_after"]
    old = {r["id"]: r for r in baseline["results"]}
    differences = [{"id": r["id"], "suite": r["suite"], "before": old.get(r["id"], {}).get("passed"), "after": r["passed"], "problems": r["problems"]} for r in units]
    report["improved"] = [r["id"] for r in differences if r["before"] is False and r["after"]]
    report["regressed"] = [r["id"] for r in differences if r["before"] and not r["after"]]
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps({key: value for key, value in report.items() if key not in {"results", "model_calls"}}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "comparison.json").write_text(json.dumps(differences, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# C 编排改造全量在线评测", "", f"模型：{report['model']}；提交：{report['commit']}；原评分器和原80单元不变。", "",
        f"原始成绩：{baseline['passed']}/{baseline['total']} → 本轮：{report['passed']}/{report['total']}（{report['pass_rate']:.1%}）。",
        f"问答请求：{report['total_requests']}；真实模型生成尝试：{len(model_calls)}；耗时：{report['wall_seconds']}秒；数据库未改动：{report['database_unchanged']}。", "",
        "| 题库 | 本轮通过 |", "| --- | --- |", *[f"| {s['suite']} | {s['passed']}/{s['total']} |" for s in suites], "",
        "## 改善与退化", "", "改善：" + "、".join(report["improved"]), "", "退化：" + ("、".join(report["regressed"]) or "无"), "",
        "## 未通过详情", ""]
    for result in units:
        if not result["passed"]:
            lines.extend([f"### {result['id']}", "", result["question"], "", *[f"- {p}" for p in result["problems"]], ""])
    lines.extend(["## 解释边界", "", "该结果是原开发集评分，不是竞争榜或盲测成绩。年份按D06明确规则前置澄清，可能与部分gold的2025默认冲突；missing_slots命名、原文数字或百分号空格也可能影响原评分。原题库/评分器未改。", "",
        "客户/商品集合/月度仅在有限粒度、条件和事实证据验收通过后解锁。文档合计仍未批准，不把片段命中当成功。模型返回、有效JSON及受控查询来源应分别阅读；有效JSON不等于语义正确SQL。详细来源及会话逐轮内容见responses.jsonl。", ""])
    (out / "评测对比与剩余问题.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"TOTAL {report['passed']}/{report['total']}; requests={len(service.responses)}; output={out}")
    return 0 if report["completed"] and report["database_unchanged"] else 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
