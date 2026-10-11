"""Diagnostic tool regression with a deliberately unavailable model; never live acceptance."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_agent_eval as evaluation
from scripts.stage2_eval import new_run, metadata, write, summaries
from backend.sql_module.errors import ModuleError
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration


class UnavailableModel:
    def __init__(self, out=None):
        self.out = out

    def generate_json(self,*args):
        if self.out:
            payload = json.loads(args[0][-1]['content'])
            with (self.out/'model-calls.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'event':'simulated_unavailable_model','question':payload.get('question'),
                    'actual_model_calls':0},ensure_ascii=False)+'\n')
        raise ModuleError('SQL_EXECUTION_FAILED','本地诊断模拟模型不可用；不是实际 API 调用','MODEL_NETWORK_FAILED')


async def main():
    out=new_run('local-controlled')
    print(out,flush=True)
    meta=metadata('local_controlled_diagnostic',sys.argv[1:])
    meta.update(actual_model_calls=0,network_policy='forbidden; unavailable model injected, urllib opener blocked')
    write(out/'run-meta.json',meta)
    (out/'model-calls.jsonl').touch(exist_ok=False)
    guard=ExitStack()
    network=guard.enter_context(patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('本地诊断禁止网络请求')))
    guard.enter_context(patch('backend.sql_module.generation.HTTPJSONModel.from_env',return_value=UnavailableModel(out)))
    try:
        integration=await create_canonical_knowledge_integration(model_mode='live')
    except BaseException:
        guard.close()
        raise
    service=integration.create_formal_graph_service()
    original=service.ask
    async def record(payload,*,request_id):
        response=await original(payload,request_id=request_id)
        with (out/'responses.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'request_id':request_id,'response':response.model_dump(mode='json')},ensure_ascii=False)+'\n')
        return response
    service.ask=record
    results=[]
    try:
        manifest=json.loads((ROOT/'eval/agent/eval_manifest.json').read_text(encoding='utf-8'))
        for name in manifest['files']:
            name=name.removesuffix('.jsonl')
            if len(sys.argv)>1 and name not in sys.argv[1].split(','):
                continue
            cases=evaluation.load_cases([name])
            batch=[]
            for case in cases:
                result=await (evaluation.run_session(service,case) if 'turns' in case else evaluation.run_single(service,case,'local_controlled_diagnostic'))
                batch.append({**result,'group':name})
            results.extend(batch)
            print(name,sum(r['passed'] for r in batch),len(batch),flush=True)
    finally:
        await integration.aclose()
        blocked=network.call_count
        guard.close()
        final=metadata('local_controlled_diagnostic',sys.argv[1:])
        meta['finished_at']=datetime.now(timezone.utc).isoformat()
        write(out/'run-meta.json',meta)
        write(out/'end-fingerprint-check.json',{key:final[key]==meta[key] for key in ('code_sha256','scorer_sha256','dataset_sha256','data_sha256')})
    report={'kind':'local_controlled_diagnostic','actual_model_calls':0,'blocked_network_attempts':blocked,
        'total':len(results),'passed':sum(r['passed'] for r in results),'results':results}
    report['pass_rate']=round(report['passed']/report['total'],4) if report['total'] else 0
    write(out/'report.json',report)
    summaries(out,report)
    print('本地工具诊断，不作为 live 验收：',report['passed'],'/',report['total'],flush=True)
    return 0 if report['passed']==report['total'] and blocked==0 else 1


if __name__=='__main__':
    sys.exit(asyncio.run(main()))
