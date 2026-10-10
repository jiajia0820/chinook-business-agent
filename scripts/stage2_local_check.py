"""Diagnostic tool regression with a deliberately unavailable model; never live acceptance."""
import asyncio
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
    def generate_json(self,*args):
        raise ModuleError('SQL_EXECUTION_FAILED','本地诊断模拟模型不可用；不是实际 API 调用','MODEL_NETWORK_FAILED')


async def main():
    out=new_run('local-controlled')
    print(out,flush=True)
    write(out/'run-meta.json',metadata('local_controlled_diagnostic',sys.argv[1:]))
    with patch('backend.sql_module.generation.HTTPJSONModel.from_env',return_value=UnavailableModel()):
        integration=await create_canonical_knowledge_integration(model_mode='live')
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
    report={'kind':'local_controlled_diagnostic','actual_model_calls':0,'total':len(results),'passed':sum(r['passed'] for r in results),'results':results}
    write(out/'report.json',report)
    (out/'model-calls.jsonl').write_text(json.dumps({'event':'simulated_unavailable_model','actual_model_calls':0})+'\n',encoding='utf-8')
    summaries(out,report)
    print('本地工具诊断，不作为 live 验收：',report['passed'],'/',report['total'],flush=True)


if __name__=='__main__':
    asyncio.run(main())
