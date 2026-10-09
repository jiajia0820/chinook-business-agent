"""横向对比多个大模型在同一评测题库上的正确率与耗时。

用法：
    python scripts/compare_models.py
    python scripts/compare_models.py --models DeepSeek-V4-Flash,MiMo-V2.6-Flash --files eval_basic_nl2sql

每个模型独立起一次评测进程（避免索引与配置串味），结果汇总成表格与 JSON。
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows GBK 控制台无法打印中文明细
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PYTHON).exists():
    PYTHON = sys.executable

DEFAULT_MODELS = [
    "DeepSeek-V4-Flash",
    "DeepSeek-V4.1-Flash",
    "DeepSeek-V4-Pro",
    "MiMo-V2.6-Flash",
    "MiniMax-M3.1-Flash",
    "Qwen3.8-Flash-Next",
    "Qwen3.8-27B",
    "glm-5.3-flash",
    "glm-5.3",
    "kimi-k3",
    "longcat-2.5",
    "step-5-preview",
]


def run_model(model: str, files: str, concurrency: int, out_dir: Path) -> dict:
    out = out_dir / f"model-{model.replace('/', '_')}.json"
    cmd = [PYTHON, str(ROOT / "scripts" / "run_agent_eval.py"), "--mode", "live",
           "--model", model, "--files", files, "--concurrency", str(concurrency),
           "--out", str(out), "--quiet"]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    wall = round(time.monotonic() - started, 1)
    if not out.exists():
        return {"model": model, "error": (proc.stderr or proc.stdout or "")[-400:], "wall_s": wall}
    report = json.loads(io.open(out, encoding="utf-8").read())
    latencies = sorted(item["elapsed_ms"] for item in report["results"] if item.get("elapsed_ms"))
    timeouts = sum(1 for item in report["results"]
                   if any("SQL_EXECUTION_FAILED" in p or "TOOL_TIMEOUT" in p for p in item["problems"]))
    degraded = sum(1 for item in report["results"]
                   if any("受控查询计划" in text for text in item.get("limitations", [])))
    return {
        "model": model,
        "total": report["total"],
        "passed": report["passed"],
        "pass_rate": report["pass_rate"],
        "boot_ms": report.get("boot_ms"),
        "avg_ms": round(statistics.mean(latencies)) if latencies else 0,
        "median_ms": round(statistics.median(latencies)) if latencies else 0,
        "min_ms": latencies[0] if latencies else 0,
        "max_ms": latencies[-1] if latencies else 0,
        "p90_ms": latencies[max(0, int(len(latencies) * 0.9) - 1)] if latencies else 0,
        "timeouts": timeouts,
        "degraded": degraded,
        "wall_s": wall,
        "per_question": {item["id"]: item["passed"] for item in report["results"]},
        "failures": {item["id"]: item["problems"][:2] for item in report["results"] if not item["passed"]},
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="多模型横向对比")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--files", default="eval_basic_nl2sql")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", default=str(ROOT / "tmp" / "model-comparison.json"))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out_dir = Path(args.out).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"题库={args.files}  模型数={len(models)}  并发={args.concurrency}")
    rows = []
    for index, model in enumerate(models, 1):
        print(f"[{index}/{len(models)}] {model} ...", flush=True)
        row = run_model(model, args.files, args.concurrency, out_dir)
        rows.append(row)
        if row.get("error"):
            print(f"    失败: {row['error'][:160]}")
        else:
            print(f"    通过 {row['passed']}/{row['total']}  平均 {row['avg_ms']}ms  最慢 {row['max_ms']}ms"
                  f"  降级 {row['degraded']}  超时 {row['timeouts']}")

    ok = [r for r in rows if not r.get("error")]
    header = f"{'模型':<24}{'通过':>7}{'正确率':>9}{'平均ms':>9}{'中位ms':>9}{'最快ms':>9}{'最慢ms':>9}{'P90ms':>9}{'降级':>6}{'超时':>6}"
    print("\n" + header)
    print("-" * len(header.expandtabs()))
    for row in sorted(ok, key=lambda r: (-r["pass_rate"], r["avg_ms"])):
        print(f"{row['model']:<24}{row['passed']}/{row['total']:<5}{row['pass_rate']*100:>7.1f}%"
              f"{row['avg_ms']:>9}{row['median_ms']:>9}{row['min_ms']:>9}{row['max_ms']:>9}{row['p90_ms']:>9}"
              f"{row['degraded']:>6}{row['timeouts']:>6}")
    for row in rows:
        if row.get("error"):
            print(f"{row['model']:<24}  调用失败: {row['error'][:120]}")

    io.open(args.out, "w", encoding="utf-8").write(
        json.dumps({"files": args.files, "concurrency": args.concurrency, "rows": rows}, ensure_ascii=False, indent=2))
    print(f"\n结果已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
