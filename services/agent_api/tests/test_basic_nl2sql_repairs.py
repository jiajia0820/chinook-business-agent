"""Behavioral regressions on real read-only data; never contact a model API."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from backend.sql_module.entity_ids import catalog_ids
from backend.sql_module.query_intent import parse_query_intent, validate_intent
from backend.sql_module.service import _controlled_query, _candidate_covers_context
from services.agent_api.app.agent.parsing import _periods, parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.profiles import ProfileRegistry
from scripts.stage2_local_check import UnavailableModel

ROOT = Path(__file__).resolve().parents[3]


def context(question):
    profile = ProfileRegistry.defaults().get('chinook-music')
    profile.sql_backend.model_mode = 'live'
    parsed = parse_question(question, profile, SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version))
    if parsed.decision != 'ready':
        raise AssertionError(parsed.model_dump())
    task = parsed.task
    return {'resolved_slots': {k:v for k,v in task.slots.items() if k in profile.sql_backend.declared_slots},
            'metric_ids': task.business_metric_ids, 'entities': task.entities}


def rows(query):
    with sqlite3.connect((ROOT/'data/chinook/Chinook.db').as_uri()+'?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query['sql'], query['params'])]


class BasicRepairUnitTests(unittest.TestCase):
    def test_identifier_forms_ranges_and_actual_year_are_separate(self):
        for label in ('TrackId 为 ', 'TrackId等于', '商品编号是', '商品ID：', 'TrackId='):
            self.assertEqual(catalog_ids(label+'2001'), [2001])
            self.assertEqual(_periods('2025年第三季度 '+label+'2001 的销售额')[0], {'year':2025,'quarter':3})
            self.assertEqual(_periods(label+'1791叫什么')[1], [])
        self.assertEqual(catalog_ids('TrackId 为 1786–1787、1791'), [1786,1787,1791])
        for text in ('TrackId 为 0', 'TrackId 20至10', 'TrackId 1至201'):
            with self.assertRaises(ValueError):
                catalog_ids(text)

    def test_catalog_varying_ids_uses_catalog_price_and_no_sales_dates(self):
        for ids in ('1','1791','2001','1786,1787'):
            ctx = context('TrackId 为 '+ids+'的商品叫什么，音乐分类、媒体类型与标价是多少美元？')
            query = _controlled_query(ctx, ctx['metric_ids'])
            self.assertTrue(_candidate_covers_context(query,ctx))
            self.assertNotIn('Invoice',query['sql'])
            self.assertNotIn('start_date',query['params'])
            values = ctx['resolved_slots']['query_intent']['track_ids']
            expected = rows({'sql':'SELECT t.TrackId,t.Name,g.Name AS genre,m.Name AS media_type,t.UnitPrice AS unit_price FROM Track t JOIN Genre g USING(GenreId) JOIN MediaType m USING(MediaTypeId) WHERE TrackId IN ('+','.join('?' for _ in values)+') ORDER BY TrackId','params':values})
            self.assertEqual(rows(query),expected)
            bad = {**query,'sql':query['sql'].replace('t.UnitPrice','0.01')}
            self.assertFalse(_candidate_covers_context(bad,ctx))
            bad = {**query,'params':{**query['params'],'track_0':99999}}
            self.assertFalse(_candidate_covers_context(bad,ctx))

    def test_sales_query_retains_identifier_and_real_period(self):
        ctx=context('2025年第三季度 TrackId 为 1791 的音频商品销售额是多少？')
        query=_controlled_query(ctx,ctx['metric_ids'])
        self.assertEqual(rows(query)[0]['TrackId'],1791)
        self.assertEqual(query['params']['start_date'],'2025-07-01')
        self.assertTrue(_candidate_covers_context(query,ctx))
        bad={**query,'sql':query['sql'].replace(' AND t.TrackId IN (:track_0)','')}
        self.assertFalse(_candidate_covers_context(bad,ctx))

    def test_customer_money_boundaries_operators_and_ties(self):
        for word, threshold, op in [('不少于',10,'>='),('不少于',15.84,'>='),('超过',15.84,'>'),('等于',15.84,'='),('低于',10,'<'),('不超过',10,'<=')]:
            q=f'2025年第三季度音频消费总额{word} {threshold} 美元的客户有哪些？列出客户编号和消费额，按消费额降序排列，同额按客户编号升序排列。'
            ctx=context(q); query=_controlled_query(ctx,ctx['metric_ids'])
            actual=[{k:r[k] for k in ('CustomerId','sales_amount')} for r in rows(query)]
            gold=rows({'sql':'SELECT i.CustomerId,ROUND(SUM(l.UnitPrice*l.Quantity),2) AS sales_amount FROM Invoice i JOIN InvoiceLine l USING(InvoiceId) JOIN Track t USING(TrackId) WHERE i.InvoiceDate>=:s AND i.InvoiceDate<:e AND t.MediaTypeId IN (1,2,4,5) GROUP BY i.CustomerId HAVING ROUND(SUM(l.UnitPrice*l.Quantity),2) '+op+' :v ORDER BY sales_amount DESC,i.CustomerId','params':{'s':'2025-07-01','e':'2025-10-01','v':threshold}})
            self.assertEqual(actual,gold)
            self.assertTrue(_candidate_covers_context(query,ctx))
            if threshold==15.84 and op=='>=':
                self.assertEqual([r['CustomerId'] for r in actual],[10,48])
            for bad in ({**query,'params':{**query['params'],'amount_threshold':threshold+1}},
                        {**query,'sql':query['sql'].replace('HAVING','AND')},
                        {**query,'sql':query['sql'].replace('sales_amount DESC','sales_amount ASC')}):
                self.assertFalse(_candidate_covers_context(bad,ctx))

    def test_unparsed_money_conditions_and_invalid_intents_fail_closed(self):
        for q in ('音频消费总额不少于十美元的客户有哪些','消费额不少于10元的客户有哪些','消费额不少于10美元且不超过20美元的客户有哪些'):
            with self.assertRaises(ValueError):
                parse_query_intent(q,['sales_amount'])
        for rule in ({'operator':'DROP','value':10},{'operator':'gte','value':True},{'operator':'gte','value':float('inf')},{'operator':'gte','value':-1}):
            self.assertFalse(validate_intent({'dimension':'customer','amount_filter':rule}))
        self.assertFalse(validate_intent({'operation':'track_attributes','track_ids':[True]}))

    def test_zero_media_preserves_all_rows_and_only_qualified_facts(self):
        for period,start,end in [('2025年第三季度','2025-07-01','2025-10-01'),('2025年第二季度','2025-04-01','2025-07-01'),('2025年','2025-01-01','2026-01-01')]:
            ctx=context(period+'所有媒体类型的销售额和销量，没有成交的类型也要列出并显示为0，按媒体类型编号升序排列。')
            query=_controlled_query(ctx,ctx['metric_ids']); actual=rows(query)
            # Independent correlated gold: excludes facts outside the requested
            # dates without sharing the production outer-join implementation.
            gold=rows({'sql':'SELECT m.MediaTypeId,m.Name,COALESCE((SELECT ROUND(SUM(l.UnitPrice*l.Quantity),2) FROM InvoiceLine l JOIN Invoice i USING(InvoiceId) JOIN Track t USING(TrackId) WHERE t.MediaTypeId=m.MediaTypeId AND i.InvoiceDate>=:s AND i.InvoiceDate<:e),0) AS sales_amount,COALESCE((SELECT SUM(l.Quantity) FROM InvoiceLine l JOIN Invoice i USING(InvoiceId) JOIN Track t USING(TrackId) WHERE t.MediaTypeId=m.MediaTypeId AND i.InvoiceDate>=:s AND i.InvoiceDate<:e),0) AS units_sold FROM MediaType m ORDER BY m.MediaTypeId','params':{'s':start,'e':end}})
            self.assertEqual(actual,gold)
            self.assertEqual(len(actual),5)
            self.assertTrue(_candidate_covers_context(query,ctx))
            for bad in ({**query,'sql':query['sql'].replace('LEFT JOIN','JOIN')},
                        {**query,'sql':query['sql'].replace('i.InvoiceDate < :end_date','i.InvoiceDate <= :end_date')},
                        {**query,'sql':query['sql'].replace('COALESCE(f.sales_amount,0)','f.sales_amount')},
                        {**query,'params':{**query['params'],'start_date':'2021-01-01'}}):
                self.assertFalse(_candidate_covers_context(bad,ctx))
        ctx=context('2025年第三季度所有媒体类型的销售额和销量，没有成交的类型也要列出并显示为0，按媒体类型编号升序排列。')
        actual=rows(_controlled_query(ctx,ctx['metric_ids']))
        self.assertEqual([r['MediaTypeId'] for r in actual if r['sales_amount']==0],[3,4,5])


class BasicRepairIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch('backend.sql_module.generation.HTTPJSONModel.from_env',return_value=UnavailableModel()):
            self.integration=await create_canonical_knowledge_integration(model_mode='live')
        self.service=self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        await self.integration.aclose()

    async def test_all_fifteen_against_frozen_scorer_with_network_forbidden(self):
        from scripts.eval_scoring import judge
        cases=[json.loads(line) for line in (ROOT/'eval/agent/eval_basic_nl2sql.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(cases),15)
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')) as network:
            for case in cases:
                with self.subTest(case=case['id']):
                    result=await self.service.ask(AskRequest(profile_id='chinook-music',question=case['question']),request_id='repair-'+case['id'])
                    self.assertEqual(judge(case,result),[],msg=result.model_dump_json())
            network.assert_not_called()

    async def test_missing_id_does_not_leak_into_year_or_catalog_query(self):
        for q in ('TrackId为0的商品叫什么','TrackId 1至99999的商品属性是什么'):
            result=await self.service.ask(AskRequest(profile_id='chinook-music',question=q),request_id='invalid-catalog')
            self.assertEqual(result.status,'clarification_required')
            self.assertEqual(result.sql_results,[])

    async def test_wrong_model_candidates_are_repaired_for_all_three_shapes(self):
        from scripts.eval_scoring import judge
        cases=[json.loads(line) for line in (ROOT/'eval/agent/eval_basic_nl2sql.jsonl').read_text(encoding='utf-8').splitlines()]
        for index in (10,13,14):
            case=cases[index]; ctx=context(case['question']); bad=_controlled_query(ctx,ctx['metric_ids'])
            if index==10:
                bad['params']['track_0']=1786
            elif index==13:
                bad['params']['amount_threshold']=0
                bad['sql']+=' LIMIT 1'
            else:
                bad['sql']=bad['sql'].replace('LEFT JOIN','JOIN')
            class WrongCandidate:
                def generate_json(self,*args):
                    return json.dumps(bad)
            self.integration.sql_tool._service.model=WrongCandidate()
            result=await self.service.ask(AskRequest(profile_id='chinook-music',question=case['question']),request_id='wrong-'+case['id'])
            self.assertEqual(judge(case,result),[],msg=result.model_dump_json())
            self.assertTrue(any('模型候选遗漏' in item for item in result.limitations))
