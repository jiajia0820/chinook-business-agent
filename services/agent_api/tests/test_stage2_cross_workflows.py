from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as N
import sqlite3
import unittest
from unittest.mock import patch

from backend.sql_module.business_queries import build_query,output_columns,valid_operation
from services.agent_api.app.agent.entity_ids import track_selection
from services.agent_api.app.agent.parsing import _periods,parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.contracts import AskRequest,DocumentChunk
from services.agent_api.app.evidence.business_workflows import resolve_sources,conclude,WorkflowGap
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.evidence.claim_comparison import ScalarClaim,compare_claims


class IdentifierTests(unittest.TestCase):
    def test_range_separators_lists_and_real_year(self):
        for sep in ['-','–','至']:
            q=f'2025年第三季度海报Rock主页面TrackId 1791{sep}1799是否成交'
            self.assertEqual(_periods(q)[1],[2025])
            self.assertEqual(track_selection(q)['ranges'],[[1791,1799]])
        self.assertEqual(_periods('第三季度TrackId 2025、2026')[1],[])
        self.assertEqual(track_selection('商品编号2025、2026')['ids'],[2025,2026])
        self.assertEqual(_periods('2025年第三季度')[1],[2025])

    def test_new_full_question_does_not_reuse_previous_workflow(self):
        p=ProfileRegistry.defaults().get('chinook-music');p.sql_backend.model_mode='live'
        m=SessionMemory(profile_id=p.profile_id,config_version=p.config_version)
        task=parse_question('第三季度购买Rock的客户，哪些属于A层重点维护？',p,m).task
        next_turn=parse_question('2025年第三季度音频销售额是多少？',p,m.model_copy(update={'last_task':task}))
        self.assertNotIn('business_workflow',next_turn.task.slots)


class QuerySemanticsTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE Customer(CustomerId INTEGER,FirstName TEXT,LastName TEXT);
CREATE TABLE Invoice(InvoiceId INTEGER,CustomerId INTEGER,InvoiceDate TEXT);
CREATE TABLE InvoiceLine(InvoiceId INTEGER,TrackId INTEGER,UnitPrice REAL,Quantity INTEGER);
CREATE TABLE Track(TrackId INTEGER,Name TEXT,GenreId INTEGER,MediaTypeId INTEGER);
INSERT INTO Customer VALUES(1,'A','a'),(2,'B','b'),(3,'C','c');
INSERT INTO Track VALUES(100,'cohort',1,1),(101,'other',3,1),(102,'video',1,3),(103,'outside',1,1);
INSERT INTO Invoice VALUES(1,1,'2025-07-01'),(2,2,'2025-08-01'),(3,3,'2025-09-01'),(4,1,'2025-10-01');
INSERT INTO InvoiceLine VALUES(1,100,2,1),(1,101,5,2),(1,102,100,1),(2,100,11.99,1),(3,101,20,1),(4,103,99,1);''')
        self.slots={'start_date':'2025-07-01','end_date':'2025-10-01','media_ids':[1,2,4,5]}

    def tearDown(self): self.db.close()

    def test_membership_and_full_audio_threshold_distinct_and_inclusive(self):
        slots={**self.slots,'query_intent':{'operation':'customer_cohort','cohort_genre_id':1}}
        query=build_query(slots)
        rows=[dict(r) for r in self.db.execute(query['sql'],query['params'])]
        self.assertEqual({r['CustomerId'] for r in rows},{1,2}) # customer 3 spent enough but no genre purchase
        self.assertEqual({r['CustomerId']:r['all_audio_sales_amount'] for r in rows},{1:12.0,2:11.99})
        result=N(status='success',truncated=False,sql=query['sql'],params=query['params'],columns=output_columns(slots['query_intent']),rows=rows,query_id='test-sql')
        sources={'intent':'customer_segmentation','slots':slots,'threshold':'12.00','rule_ref':'D04-test'}
        audit=conclude(sources,N(status='success',sql_results=[result]))
        self.assertEqual([r['CustomerId'] for r in audit['decisions'] if r['eligible']],[1])
        sources['threshold']='13.00' # threshold comes from the source, not code
        self.assertFalse(any(r['eligible'] for r in conclude(sources,N(status='success',sql_results=[result]))['decisions']))

    def test_zero_candidates_quarter_boundary_and_other_media(self):
        slots={**self.slots,'query_intent':{'operation':'document_candidates','candidate_ids':[100,103],'genre_id':1}}
        query=build_query(slots)
        rows=[dict(r) for r in self.db.execute(query['sql'],query['params'])]
        self.assertEqual([(r['TrackId'],r['sales_amount'],r['order_count']) for r in rows],[(100,13.99,2),(103,0,0)])
        self.assertNotIn(102,{r['TrackId'] for r in rows})

    def test_operation_limits_reject_unbounded_or_extra_inputs(self):
        self.assertFalse(valid_operation({'operation':'document_candidates','candidate_ids':[],'genre_id':1}))
        self.assertFalse(valid_operation({'operation':'customer_cohort','cohort_genre_id':True}))
        self.assertFalse(valid_operation({'operation':'customer_cohort','cohort_genre_id':1,'sql':'SELECT 1'}))

    def test_source_condition_changes_and_truncation_cannot_reuse_decision(self):
        slots={**self.slots,'query_intent':{'operation':'customer_cohort','cohort_genre_id':1}}
        query=build_query(slots)
        result=N(status='success',truncated=True,sql=query['sql'],params=query['params'],columns=output_columns(slots['query_intent']),rows=[],query_id='test-sql')
        sources={'intent':'customer_segmentation','slots':slots,'threshold':'12.00','rule_ref':'D04-test'}
        with self.assertRaises(WorkflowGap): conclude(sources,N(status='success',sql_results=[result]))
        result.truncated=False;result.params={**query['params'],'start_date':'2025-04-01'}
        with self.assertRaises(WorkflowGap): conclude(sources,N(status='success',sql_results=[result]))


class ClaimComparisonTests(unittest.TestCase):
    def claim(self,**updates):
        data=dict(metric='sales_amount',entity='all_audio',period='2025-08',unit='USD',scope=(1,2,4,5),role='actual',value=Decimal('37.62'),source='SQL')
        return ScalarClaim(**{**data,**updates})

    def test_same_scope_conflict_retains_sources_and_difference(self):
        r=compare_claims(self.claim(),self.claim(value=Decimal('36.63'),source='D08#P1'))
        self.assertEqual(r['status'],'conflict');self.assertEqual(r['difference'],'0.99')
        self.assertEqual(r['sources'],['SQL','D08#P1'])

    def test_target_difference_and_mismatched_scope_are_not_conflicts(self):
        self.assertEqual(compare_claims(self.claim(),self.claim(role='target',value=Decimal('40')))['status'],'business_gap')
        for changes in [dict(unit='件'),dict(scope=(1,)),dict(period='2025-07'),dict(entity='Genre:1'),dict(metric='units_sold')]:
            self.assertEqual(compare_claims(self.claim(),self.claim(**changes))['status'],'not_comparable')


class WorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from services.agent_api.app.knowledge.index import load_knowledge_index
        cls.index=load_knowledge_index(Path('data/knowledge').resolve(),ProfileRegistry.defaults().get('chinook-music'))

    async def asyncSetUp(self):
        from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
        # Neither flow may use a model's output instead of document dependencies.
        class NoModel:
            def generate_json(self,*args): raise AssertionError('business flow called model')
        with patch('services.agent_api.app.integrations.sql_d12.load_knowledge_index',return_value=self.index),patch('backend.sql_module.generation.HTTPJSONModel.from_env',return_value=NoModel()):
            self.integration=await create_canonical_knowledge_integration(model_mode='live')
        self.service=self.integration.create_formal_graph_service()

    async def asyncTearDown(self): await self.integration.aclose()

    async def ask(self,q,**options):
        return await self.service.ask(AskRequest(question=q,profile_id='chinook-music',options=options),request_id='workflow-test')

    async def test_actual_cohort_amounts_and_applied_rule(self):
        r=await self.ask('第三季度购买Rock的客户，哪些属于A层重点维护？')
        self.assertEqual(r.status,'answered')
        rows=r.sql_results[0].rows
        self.assertEqual({x['CustomerId'] for x in rows if Decimal(str(x['all_audio_sales_amount']))>=12},{10,31,48})
        self.assertEqual(len(rows),12)
        self.assertIn('全部音频',r.answer)

    async def test_actual_candidate_intersection(self):
        r=await self.ask('海报Rock主页面TrackId 1791–1799第三季度是否成交？')
        self.assertEqual(r.status,'answered')
        self.assertEqual([x['TrackId'] for x in r.sql_results[0].rows],[1791,1793,1795,1797,1799])
        self.assertEqual({x.doc_id for x in r.documents},{'D05','D11'})
        self.assertTrue(any(x.bbox is not None for x in r.documents if x.doc_id=='D11'))

    async def test_empty_intersection_is_not_zero_sales(self):
        r=await self.ask('2025年第三季度海报Rock主页面TrackId 1–9是否成交？')
        self.assertEqual(r.status,'answered');self.assertFalse(r.sql_results)
        self.assertIn('无交集',r.answer);self.assertIn('不能',r.answer)

    async def test_missing_rule_or_candidate_or_poster_is_insufficient(self):
        base=self.index
        class Filtered:
            def __init__(self,excluded): self.excluded=excluded
            def retrieve(self,request):
                r=base.retrieve(request)
                return r.model_copy(update={'chunks':[c for c in r.chunks if c.doc_id!=self.excluded]})
        for excluded,q in [('D04','第三季度购买Rock的客户，哪些属于A层重点维护？'),('D05','海报Rock主页面TrackId 1791-1799第三季度是否成交？'),('D11','海报Rock主页面TrackId 1791-1799第三季度是否成交？')]:
            self.integration.rag_tool.index=Filtered(excluded)
            r=await self.ask(q)
            self.assertEqual(r.status,'insufficient_evidence',excluded)
            self.assertFalse(r.sql_results,excluded)

    async def test_truncation_and_wrong_rule_period_not_complete_answer(self):
        r=await self.ask('第三季度购买Rock的客户，哪些属于A层重点维护？',max_rows=1)
        self.assertEqual(r.status,'insufficient_evidence');self.assertIn('截断',r.answer)
        r=await self.ask('2025年第二季度购买Rock的客户，哪些属于A层重点维护？')
        self.assertEqual(r.status,'insufficient_evidence');self.assertIn('期间',r.answer)

    async def test_sql_failure_not_zero_sales(self):
        from backend.sql_module.errors import ModuleError
        with patch.object(self.integration.sql_tool._service.executor,'execute_sql',side_effect=ModuleError('SQL_EXECUTION_FAILED','test failure')):
            r=await self.ask('海报Rock主页面TrackId 1791-1799第三季度是否成交？')
        self.assertEqual(r.status,'error');self.assertIsNotNone(r.error)
        self.assertNotIn('零成交',r.answer)


if __name__=='__main__': unittest.main()
