from contextlib import closing
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from backend.sql_module import Registry, AccessContext
from backend.sql_module.service import NLQueryService
from backend.sql_module.generation import DemoJSONModel, HTTPJSONModel, decode_generated
from backend.sql_module.errors import ModuleError
from backend.sql_module.profiles import validate_profile

ROOT=Path(__file__).resolve().parents[1]


class StubModel:
    def __init__(self,payload):
        self.payload,self.calls=payload,[]
    def generate_json(self,messages,output_schema,deadline):
        self.calls.append((messages,output_schema,deadline))
        return json.dumps(self.payload) if isinstance(self.payload,dict) else self.payload


class BasicNLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=Registry.load()
        cls.access=AccessContext.local(cls.registry)
        cls.path=ROOT/'data/chinook/Chinook.db'
        cls.before=hashlib.sha256(cls.path.read_bytes()).digest()
    @classmethod
    def tearDownClass(cls):
        assert cls.before==hashlib.sha256(cls.path.read_bytes()).digest()
    def ask(self,model,**extra):
        request=dict(request_id='req',profile_id='chinook',question='客户数量是多少？')
        request.update(extra)
        return NLQueryService(self.registry,model).answer_sql(request,self.access)

    def test_single_candidate_real_chinook_count(self):
        model=StubModel(dict(sql='SELECT COUNT(*) AS n FROM Customer',params={}))
        r=self.ask(model,context={'metric_ids':['customer_count']})
        self.assertEqual(r['status'],'answered',r)
        self.assertEqual(r['sql_results'][0]['rows'],[{'n':59}])
        self.assertEqual(set(r),{'request_id','profile_id','status','sql_results','query_artifacts','metric_definitions','limitations','clarification','trace','error'})
        self.assertEqual(r['query_artifacts'][0]['attempt_count'],1)
        self.assertIn('尚未由 A',r['metric_definitions'][0]['definition'])
        self.assertEqual(len(model.calls),1)
        payload=json.loads(model.calls[0][0][-1]['content'])
        self.assertEqual(len(payload['schema']['tables']),11)
        self.assertNotIn('sqlite_path',json.dumps(payload))

    def test_params_empty_and_truncation(self):
        r=self.ask(StubModel(dict(sql='SELECT Name FROM Genre WHERE Name=:name',params={'name':"Rock' OR 1=1 --"})))
        self.assertEqual(r['status'],'answered',r)
        self.assertEqual(r['sql_results'][0]['rows'],[])
        r=self.ask(StubModel(dict(sql='SELECT GenreId FROM Genre ORDER BY GenreId',params={})),options={'max_rows':2,'show_trace':False})
        self.assertEqual(r['status'],'answered',r)
        self.assertTrue(r['sql_results'][0]['truncated'])
        self.assertEqual(r['trace'],[])

    def test_bad_model_outputs_no_execution(self):
        outputs=['```json\n{}\n```','not json','{"sql":"SELECT 1","params":{},"sql":"DELETE FROM Genre"}',
                 {'sql':'SELECT 1','params':{},'extra':'x'},{'sql':'SELECT 1','params':{'x':[]}}]
        for payload in outputs:
            with self.subTest(payload=payload):
                service=NLQueryService(self.registry,StubModel(payload))
                service.executor=Mock()
                r=service.answer_sql(dict(request_id='r',profile_id='chinook',question='q'),self.access)
                self.assertEqual(r['status'],'error')
                self.assertEqual(r['error']['details']['reason'],'MODEL_OUTPUT_INVALID')
                service.executor.execute_sql.assert_not_called()

    def test_unsafe_generated_sql_is_error_no_repair(self):
        for sql,params in [('DELETE FROM Genre',{}),('SELECT Missing FROM Genre',{}),('SELECT :x AS n',{})]:
            model=StubModel(dict(sql=sql,params=params))
            r=self.ask(model)
            self.assertEqual(r['status'],'error',r)
            self.assertEqual(r['error']['code'],'SQL_EXECUTION_FAILED')
            self.assertEqual(len(model.calls),1)
            self.assertEqual(r['query_artifacts'],[])
            self.assertEqual(r['sql_results'][0]['rows'],[])

    def test_invalid_request_before_model(self):
        extras=[{'question':' '},{'question':True},{'options':{'max_rows':True}},
                {'options':{'show_trace':'yes'}},{'extra':1},
                {'context':{'resolved_slots':{'year':2025}}}, {'context':{'metric_ids':['unknown']}},
                {'context':{'business_context':[{'ref_id':'x','text':True}]}},
                {'context':{'reference_time':'2026-10-03'}}]
        for extra in extras:
            model=StubModel(dict(sql='SELECT 1',params={}))
            r=self.ask(model,**extra)
            self.assertEqual(r['error']['code'],'INVALID_REQUEST',r)
            self.assertFalse(model.calls)

    def test_unknown_and_missing_model(self):
        self.assertEqual(self.ask(StubModel({}),profile_id='unknown')['error']['code'],'PROFILE_NOT_FOUND')
        with patch.dict(os.environ,{},clear=True):
            r=self.ask(None)
        self.assertEqual(r['error']['details']['reason'],'CONFIG_MISSING',r)

    def test_timed_out_model_no_execution_and_connection_error_mapping(self):
        service=NLQueryService(self.registry,StubModel(dict(sql='SELECT 1 AS n',params={})))
        request=dict(request_id='r',profile_id='chinook',question='q')
        with patch.object(service.model,'generate_json',side_effect=ModuleError('SQL_EXECUTION_FAILED','超时','MODEL_TIMEOUT')):
            service.executor=Mock()
            r=service.answer_sql(request,self.access)
            self.assertEqual(r['error']['details']['reason'],'MODEL_TIMEOUT')
            service.executor.execute_sql.assert_not_called()
        service=NLQueryService(self.registry,StubModel(dict(sql='SELECT 1 AS n',params={})))
        error=ModuleError('PROFILE_UNAVAILABLE','连接失败','CONNECTION_FAILED').as_dict()
        service.executor.execute_sql=Mock(return_value=dict(status='failed',error=error,execution_ms=1))
        r=service.answer_sql(request,self.access)
        self.assertEqual(r['error']['code'],'PROFILE_UNAVAILABLE',r)

    def test_model_failure_degrades_to_controlled_plan(self):
        class FailingModel:
            is_demo=False
            def generate_json(self,messages,output_schema,deadline):
                raise ModuleError('SQL_EXECUTION_FAILED','模型服务返回 HTTP 504','MODEL_HTTP_FAILED',True)

        request=dict(request_id='r',profile_id='chinook',question='2025年第三季度音频销售额是多少？',
                     context=dict(resolved_slots=dict(start_date='2025-07-01',end_date='2025-10-01',media_ids=[1,2,4,5]),
                                  metric_ids=['sales_amount']))
        r=NLQueryService(self.registry,FailingModel()).answer_sql(request,self.access)
        self.assertEqual(r['status'],'answered',r)
        self.assertEqual(r['sql_results'][0]['rows'],[{'sales_amount':112.86}])
        self.assertTrue(any('受控查询计划' in line and '模型调用' in line for line in r['limitations']),r['limitations'])
        self.assertTrue(any('受控查询计划' in (step.get('summary') or '') for step in r['trace']),r['trace'])

    def test_model_failure_without_registered_metrics_still_errors(self):
        class FailingModel:
            is_demo=False
            def generate_json(self,messages,output_schema,deadline):
                raise ModuleError('SQL_EXECUTION_FAILED','模型调用超时','MODEL_TIMEOUT')

        # 没有已登记指标就没有确定性口径可用，此时必须如实失败，不能猜一条 SQL。
        r=NLQueryService(self.registry,FailingModel()).answer_sql(
            dict(request_id='r',profile_id='chinook',question='随便问点什么'),self.access)
        self.assertEqual(r['status'],'error',r)
        self.assertEqual(r['error']['details']['reason'],'MODEL_TIMEOUT',r)
        self.assertEqual(r['sql_results'],[])

    def test_config_failure_does_not_degrade(self):
        class UnconfiguredModel:
            is_demo=False
            def generate_json(self,messages,output_schema,deadline):
                raise ModuleError('PROFILE_UNAVAILABLE','模型配置不可用','CONFIG_MISSING')

        request=dict(request_id='r',profile_id='chinook',question='2025年第三季度音频销售额是多少？',
                     context=dict(resolved_slots=dict(start_date='2025-07-01',end_date='2025-10-01',media_ids=[1,2,4,5]),
                                  metric_ids=['sales_amount']))
        r=NLQueryService(self.registry,UnconfiguredModel()).answer_sql(request,self.access)
        self.assertEqual(r['status'],'error',r)
        self.assertEqual(r['error']['details']['reason'],'CONFIG_MISSING',r)
        self.assertEqual(r['sql_results'],[])

    def test_demo_explicit_label_and_supported_examples(self):
        for question,value in [('客户数量是多少？',59),('美国客户数量是多少？',13)]:
            r=self.ask(DemoJSONModel(),question=question)
            self.assertEqual(r['status'],'answered',r)
            self.assertEqual(r['sql_results'][0]['rows'][0]['customer_count'],value)
            self.assertTrue(any('离线演示' in line for line in r['limitations']))
        self.assertEqual(self.ask(DemoJSONModel(),question='不支持的问法')['status'],'unsupported')

    def test_access_context_prompt_isolation(self):
        model=StubModel(dict(sql='SELECT Name FROM Genre',params={}))
        r=NLQueryService(self.registry,model).answer_sql(dict(request_id='r',profile_id='chinook',question='类型'),AccessContext({'chinook':{'main.Genre'}}))
        # Configured metrics refer to Customer; fail closed rather than leaking its catalog to a narrower principal.
        self.assertEqual(r['error']['details']['reason'],'CATALOG_MISMATCH',r)
        self.assertFalse(model.calls)


class FixtureAndCatalogTests(unittest.TestCase):
    def test_three_row_fixture_and_missing_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            directory=Path(directory)
            path=directory/'fixture.db'
            with closing(sqlite3.connect(path)) as c:
                c.executescript('CREATE TABLE Customer(id INTEGER PRIMARY KEY);INSERT INTO Customer VALUES(1),(2),(3);')
            metrics=dict(version='test-1',metrics=[dict(metric_id='count',name='数量',definition='测试夹具计数',required_columns=['main.Customer.id'],required_slots=['year'],source_refs=['fixture'])])
            (directory/'metrics.json').write_text(json.dumps(metrics),encoding='utf-8')
            data=dict(profile_id='fixture',mode='sql_only',config_version='1',database=dict(dialect='sqlite',sqlite_path=str(path),display_name='夹具',allowed_schemas=['main'],allowed_tables=['main.Customer']),catalog_files={'metrics':'metrics.json'},slots={'year':{'type':'integer'}})
            registry=Registry([validate_profile(data,directory)])
            model=StubModel(dict(sql='SELECT COUNT(*) AS n FROM Customer',params={}))
            service=NLQueryService(registry,model)
            request=dict(request_id='r',profile_id='fixture',question='测试夹具客户数',context={'metric_ids':['count']})
            r=service.answer_sql(request,AccessContext.local(registry))
            self.assertEqual(r['error']['code'],'MISSING_REQUIRED_SLOT')
            self.assertFalse(model.calls)
            request['context']['resolved_slots']={'year':2025}
            r=service.answer_sql(request,AccessContext.local(registry))
            self.assertEqual(r['status'],'answered',r)
            self.assertEqual(r['sql_results'][0]['rows'],[{'n':3}])
            # Demonstrates plumbing, not whether the model respected year semantics.


class ModelHTTPTests(unittest.TestCase):
    def test_http_wire_mock(self):
        content=json.dumps({'sql':'SELECT 1 AS n','params':{}})
        response=io.BytesIO(json.dumps({'choices':[{'message':{'content':content}}]}).encode())
        opener=Mock()
        opener.open.return_value=response
        model=HTTPJSONModel('https://example.invalid/chat/completions','test','private-key',opener)
        result=model.generate_json([{'role':'user','content':'q'}],{},time.monotonic()+5)
        self.assertEqual(decode_generated(result)['sql'],'SELECT 1 AS n')
        request=opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['response_format'],{'type':'json_object'})
        self.assertEqual(request.get_header('Authorization'),'Bearer private-key')

    def test_http_errors_sanitized(self):
        for exc,reason in [(HTTPError('https://private',401,'key-secret',{},None),'MODEL_HTTP_FAILED'),
                           (URLError('key-secret'),'MODEL_NETWORK_FAILED'),(TimeoutError('key-secret'),'MODEL_TIMEOUT')]:
            opener=Mock()
            opener.open.side_effect=exc
            with self.assertRaises(ModuleError) as error:
                HTTPJSONModel('https://example.invalid','m','secret',opener).generate_json([],{},time.monotonic()+5)
            self.assertEqual(error.exception.reason,reason)
            self.assertNotIn('secret',str(error.exception))

    def test_env_url_validation(self):
        for endpoint in ['http://example.invalid','https://u:p@example.invalid/x','https://example.invalid/?secret=x']:
            with patch.dict(os.environ,{'C_SQL_MODEL_ENDPOINT':endpoint,'C_SQL_MODEL_NAME':'m','C_SQL_MODEL_API_KEY':'secret'}):
                with self.assertRaises(ModuleError): HTTPJSONModel.from_env()

    def test_bad_wire_response_and_expired_deadline(self):
        opener=Mock()
        opener.open.return_value=io.BytesIO(b'{"choices":[]}')
        model=HTTPJSONModel('https://example.invalid','m','fake',opener)
        with self.assertRaises(ModuleError) as error:
            model.generate_json([],{},time.monotonic()+5)
        self.assertEqual(error.exception.reason,'MODEL_RESPONSE_INVALID')
        opener.reset_mock()
        with self.assertRaises(ModuleError): model.generate_json([],{},time.monotonic()-1)
        opener.open.assert_not_called()

    def test_real_model(self):
        if os.environ.get('C_RUN_REAL_SQL_MODEL')!='1':
            self.skipTest('未显式启用真实模型测试；模拟成功不代表实际模型效果')
        model=HTTPJSONModel.from_env()  # opted-in missing config is a failure, not a skip
        registry=Registry.load()
        r=NLQueryService(registry,model).answer_sql(dict(request_id='integration',profile_id='chinook',question='请统计 Customer 表全部客户记录数量，输出 customer_count。',context={'metric_ids':['customer_count']}),AccessContext.local(registry))
        self.assertEqual(r['status'],'answered',r)
        self.assertEqual(r['sql_results'][0]['rows'],[{'customer_count':59}])


class CLIStage3Tests(unittest.TestCase):
    def test_actual_demo_cli_and_query_file(self):
        args=[sys.executable,'-m','backend.sql_module','ask','chinook','客户数量是多少？','--demo-model']
        run=subprocess.run(args,cwd=ROOT,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr+run.stdout)
        self.assertEqual(json.loads(run.stdout)['status'],'answered')
        run=subprocess.run([sys.executable,'-m','backend.sql_module','ask','chinook','--request-file',str(ROOT/'examples/c-nl-query.json'),'--demo-model'],cwd=ROOT,capture_output=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr+run.stdout)
        self.assertEqual(json.loads(run.stdout)['metric_definitions'][0]['metric_id'],'customer_count')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'request.json'
            path.write_text(json.dumps(dict(query_id='q',profile_id='chinook',sql='SELECT 1 AS n')),encoding='utf-8')
            run=subprocess.run([sys.executable,'-m','backend.sql_module','query','--request-file',str(path)],cwd=ROOT,capture_output=True,encoding='utf-8')
            self.assertEqual(run.returncode,0,run.stderr+run.stdout)
            self.assertEqual(json.loads(run.stdout)['rows'],[{'n':1}])


if __name__=='__main__': unittest.main()
