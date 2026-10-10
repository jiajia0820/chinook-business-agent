"""Append-only, redacted evidence for live eval or historical rescoring."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import scripts.run_agent_eval as evaluation
from scripts import eval_scoring


def safe_json(value):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    for name, secret in os.environ.items():
        if any(word in name.upper() for word in ('API_KEY', 'TOKEN', 'PASSWORD')) and len(secret) >= 8:
            text = text.replace(secret, '<redacted>')
    return text


def write(path, value):
    path.write_text(safe_json(value), encoding='utf-8')


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def new_run(kind):
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + kind + '-' + uuid.uuid4().hex[:8]
    out = ROOT / 'tmp' / 'stage2-eval' / run_id
    out.mkdir(parents=True, exist_ok=False)
    return out


def metadata(kind, argv):
    git = ['git', '-c', f'safe.directory={ROOT}', '-C', str(ROOT)]
    code = [p for folder in ('backend', 'services/agent_api/app', 'config', 'profiles') for p in (ROOT/folder).rglob('*') if p.suffix in ('.py', '.json') and '__pycache__' not in p.parts]
    return {'kind': kind, 'started_at': datetime.now(timezone.utc).isoformat(),
        'git_head': subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip(),
        'git_status': subprocess.check_output(git + ['status', '--porcelain'], text=True),
        'code_sha256': fingerprint(code),
        'scorer_version': eval_scoring.VERSION,
        'scorer_sha256': fingerprint([ROOT/'scripts/eval_scoring.py', ROOT/'scripts/run_agent_eval.py']),
        'dataset_sha256': fingerprint((ROOT/'eval/agent').glob('*.json*')),
        'data_sha256': fingerprint([p for p in (ROOT/'data').rglob('*') if p.is_file() and p.suffix in ('.sqlite', '.db', '.pdf', '.png', '.md', '.json')]),
        'model': os.environ.get('LLM_MODEL'), 'endpoint': os.environ.get('LLM_BASE_URL'),
        'argv': argv, 'python': sys.version, 'concurrency_default': 1}


def summaries(out, report, *, historical=False):
    failures = ['# 历史响应重评分' if historical else '# 本轮失败清单']
    for item in report['results']:
        if not item['passed']:
            failures.append(f"\n## {item['id']}\n" + '\n'.join('- ' + p for p in item['problems']))
    (out/'failures.md').write_text('\n'.join(failures), encoding='utf-8')
    (out/'comparison.md').write_text('# 对比说明\n\n' + ('仅重新评分历史响应，没有重新调用模型，不代表系统能力提升。' if historical else '本次独立运行；不得拼接不同运行的最好成绩。对比版本与新增通过/退化在阶段进度记录中说明。'), encoding='utf-8')


def historical(out):
    from services.agent_api.app.contracts import AskResponse
    records = [json.loads(line) for line in (ROOT/'tmp/local-eval/live-responses.jsonl').read_text(encoding='utf-8').splitlines()]
    indexed = {r['request_id']: r for r in records}
    old = json.loads((ROOT/'tmp/local-eval/live-full.json').read_text(encoding='utf-8'))
    old_scores = {r['id']: r for r in old['results']}
    results = []
    for name in old['files']:
        for case in evaluation.load_cases([name]):
            problems, diagnostics = [], []
            turns = case.get('turns') or [case]
            for turn in turns:
                turn_id = f"{case['id']}-t{turn['turn']}" if 'turn' in turn else case['id']
                record = indexed.get('eval-' + turn_id)
                if record is None:
                    problems.append(f'{turn_id}: 缺少历史响应')
                    continue
                response = AskResponse.model_validate(record['response'])
                problems.extend(f'{turn_id}: {p}' for p in eval_scoring.judge({**turn, 'id': turn_id}, response))
                diagnostics.extend(eval_scoring.interface_diagnostics(turn, response))
            results.append({'id': case['id'], 'group': name, 'old_passed': old_scores[case['id']]['passed'],
                'passed': not problems, 'problems': problems, 'interface_diagnostics': diagnostics,
                'old_problems': old_scores[case['id']]['problems']})
    report = {'kind': 'historical_rescore', 'total': len(results), 'passed': sum(r['passed'] for r in results),
              'old_passed': old['passed'], 'results': results}
    write(out/'report.json', report)
    # Copy complete prior responses; calls were not repeated.
    (out/'responses.jsonl').write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records)+'\n', encoding='utf-8')
    (out/'model-calls.jsonl').write_text('', encoding='utf-8')
    summaries(out, report, historical=True)
    changes = [r for r in results if r['old_passed'] != r['passed']]
    with (out/'comparison.md').open('a', encoding='utf-8') as stream:
        stream.write(f"\n\n旧评分 {old['passed']}/80 → 新评分 {report['passed']}/80。\n")
        for r in changes:
            stream.write(f"\n- {r['id']}: {r['old_passed']} → {r['passed']}；旧原因 {r['old_problems']}；新原因 {r['problems']}\n")
    return report


async def live(out, argv, failure_budget=0):
    from backend.sql_module.generation import HTTPJSONModel
    from backend.sql_module.errors import ModuleError
    from services.agent_api.app.services.graph_ask_service import GraphAskService
    lock = threading.Lock()
    attempts = {}
    external_failures = []
    original_ask, original_generate = GraphAskService.ask, HTTPJSONModel.generate_json

    def append(name, entry):
        with lock, (out/name).open('a', encoding='utf-8') as stream:
            stream.write(safe_json(entry).replace('\n', '') + '\n')

    async def record_ask(self, payload, *, request_id):
        started = time.monotonic()
        try:
            response = await original_ask(self, payload, request_id=request_id)
            append('responses.jsonl', {'request_id': request_id, 'question': payload.question,
                'response': response.model_dump(mode='json'), 'elapsed_ms': round((time.monotonic()-started)*1000)})
            fallback = [x for x in response.limitations if '受控查询' in x or '降级' in x]
            if fallback:
                append('model-calls.jsonl', {'request_id': request_id, 'event': 'controlled_fallback', 'details': fallback})
            return response
        except Exception as exc:
            append('responses.jsonl', {'request_id': request_id, 'question': payload.question,
                'exception_type': type(exc).__name__, 'elapsed_ms': round((time.monotonic()-started)*1000)})
            raise

    def record_generate(self, messages, output_schema, deadline):
        started = time.monotonic()
        try:
            payload = json.loads(messages[-1]['content'])
        except (ValueError, KeyError):
            payload = {}
        entry = {'event': 'model_attempt', 'question': payload.get('question'),
                 'model': os.environ.get('LLM_MODEL'), 'endpoint': os.environ.get('LLM_BASE_URL')}
        with lock:
            attempts[entry['question']] = attempts.get(entry['question'], 0) + 1
            entry['question_attempt_ordinal'] = attempts[entry['question']]
        if failure_budget and len(external_failures) >= failure_budget:
            append('model-calls.jsonl', {**entry, 'event': 'deferred_external_api',
                'status': 'not_called', 'reason': external_failures[-1], 'elapsed_ms': 0})
            raise ModuleError('SQL_EXECUTION_FAILED', '本轮外部 API 累计失败已达预算；记录待复测，继续检查受控工具。', external_failures[-1])
        try:
            result = original_generate(self, messages, output_schema, deadline)
            entry['status'] = 'success'
            return result
        except Exception as exc:
            entry.update(status='error', error_type=type(exc).__name__, reason=getattr(exc, 'reason', None), code=getattr(exc, 'code', None))
            if getattr(exc, 'reason', None) in {'MODEL_TIMEOUT', 'MODEL_NETWORK_FAILED', 'MODEL_HTTP_FAILED'}:
                external_failures.append(exc.reason)
            raise
        finally:
            entry['elapsed_ms'] = round((time.monotonic()-started)*1000)
            append('model-calls.jsonl', entry)

    GraphAskService.ask, HTTPJSONModel.generate_json = record_ask, record_generate
    for name in ('responses.jsonl', 'model-calls.jsonl'):
        (out/name).touch(exist_ok=False)
    sys.argv = [sys.argv[0], *argv, '--out', str(out/'report.json')]
    try:
        exit_code = await evaluation.main()
    finally:
        GraphAskService.ask, HTTPJSONModel.generate_json = original_ask, original_generate
    report = json.loads((out/'report.json').read_text(encoding='utf-8'))
    summaries(out, report)
    return exit_code


if __name__ == '__main__':
    argv = sys.argv[1:]
    failure_budget = 0
    if '--model-failure-budget' in argv:
        index = argv.index('--model-failure-budget')
        failure_budget = int(argv[index+1])
        del argv[index:index+2]
    kind = 'historical' if '--rescore-history' in argv else 'live'
    out = new_run(kind)
    meta = metadata(kind, argv)
    meta['external_api_failure_budget'] = failure_budget
    write(out/'run-meta.json', meta)
    print(f'证据目录：{out}', flush=True)
    try:
        if kind == 'historical':
            report = historical(out)
            print(f"历史重评分：{report['old_passed']}/{report['total']} → {report['passed']}/{report['total']}（未调用模型）")
            code = 0
        else:
            code = asyncio.run(live(out, argv, failure_budget))
    finally:
        meta['finished_at'] = datetime.now(timezone.utc).isoformat()
        write(out/'run-meta.json', meta)
    sys.exit(code)
