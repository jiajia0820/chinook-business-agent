from decimal import Decimal
from types import SimpleNamespace as N
import unittest

from backend.sql_module.service import _controlled_query, _candidate_covers_context
from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.contracts import DocumentChunk, RetrievalResponse, TimeRange
from services.agent_api.app.evidence.binding import authorize_calculation, task_period
from services.agent_api.app.integrations.business_calculation import create_business_resolver, quarterly_targets
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.local_calculator import calculate_decimal
from services.agent_api.app.tools.models import CalculationRequest, ToolFailure


class DecimalOperationsTests(unittest.TestCase):
    def request(self, function, values, unit):
        return CalculationRequest(calculation_id='calc-test',profile_id='chinook-music',function=function,
            formula_ref='formula',result_unit=unit,
            operands={key: {'value':Decimal(value),'unit':operand_unit,'source_ref':key}
                for key,(value,operand_unit) in values.items()})

    def test_average_rounding_zero_denominator_and_units(self):
        request=self.request('average_order_value',{'sales':('1.25','USD'),'orders':('2','单')},'USD/单')
        self.assertEqual(calculate_decimal(request),Decimal('0.63'))
        for orders,unit in [('0','单'),('2','件'),('1.5','单')]:
            with self.subTest(orders=orders,unit=unit),self.assertRaises(ToolFailure):
                calculate_decimal(self.request('average_order_value',{'sales':('1.25','USD'),'orders':(orders,unit)},'USD/单'))

    def test_sum_month_names_units_and_decimal_precision(self):
        request=self.request('sum',{'month_2026_04':('0.1','USD'),'month_2026_05':('0.2','USD')},'USD')
        self.assertEqual(calculate_decimal(request),Decimal('0.30'))
        for name,unit in [('month_2026_13','USD'),('month_2026_05','件'),('actual','USD')]:
            with self.subTest(name=name,unit=unit),self.assertRaises(ToolFailure):
                calculate_decimal(self.request('sum',{'month_2026_04':('0.1','USD'),name:('2',unit)},'USD'))


class BusinessBindingTests(unittest.TestCase):
    def setUp(self):
        self.profile=ProfileRegistry.defaults().get('chinook-music')
        text='附件2 月度分解\n统计范围\n4月金额\n5月金额\n6月金额\n季度金额\n全部音频\n1.25\n2.50\n3.75\n7.50'
        self.chunk=DocumentChunk(chunk_id='D06-test',doc_id='D06',title='2026Q2经营目标表',doc_type='pdf',
            page=2,text=text,source_uri='data/knowledge/D06-test.pdf#P2',retrieval_id='retrieval-test')
        self.rag=RetrievalResponse(retrieval_id='retrieval-test',profile_id=self.profile.profile_id,status='success',chunks=[self.chunk])
        self.task=BusinessTask(original_question='2026年第二季度全音频月度计划合计',normalized_question='月度计划合计',
            intent='document_sum',route='rag',needs_calculation=True,business_metric_ids=['sales_amount'],
            slots={'year':2026,'quarter':2,'media_ids':[1,2,4,5]},
            time_range=TimeRange(start='2026-04-01',end='2026-07-01',end_inclusive=False,label='2026 Q2'))
        self.context=ToolContext(request_id='request-test',session_id='session-test',profile_id=self.profile.profile_id,
            available_source_refs=(self.chunk.chunk_id,))
        index=N(profile=self.profile,documents={'D06':N(title=self.chunk.title)})
        self.resolve=create_business_resolver(index)
        self.bundle=self.resolve(self.task,None,self.rag,'calc-test',self.context)

    def authorize(self,bundle,**changes):
        return authorize_calculation(bundle,task=changes.get('task',self.task),sql=None,rag=changes.get('rag',self.rag),
            profile=self.profile,context=changes.get('context',self.context),calculation_id='calc-test',expected_mode='verified_business')

    def test_monthly_plan_uses_table_values_and_rejects_role_remapping(self):
        authorized=self.authorize(self.bundle)
        self.assertEqual(calculate_decimal(authorized.request),Decimal('7.50'))
        bad=self.bundle.model_copy(deep=True)
        bad.operands['month_2026_04'],bad.operands['month_2026_05']=bad.operands['month_2026_05'],bad.operands['month_2026_04']
        with self.assertRaises(ValueError):
            self.authorize(bad)

    def test_changed_source_or_turn_cannot_replay_binding(self):
        rag=self.rag.model_copy(deep=True)
        rag.chunks[0].text=rag.chunks[0].text.replace('1.25','9.25')
        with self.assertRaises(ValueError):
            self.authorize(self.bundle,rag=rag)
        with self.assertRaises(ValueError):
            self.authorize(self.bundle,context=self.context.model_copy(update={'session_id':'other-session'}))

    def test_other_period_does_not_inherit_operating_target(self):
        task=self.task.model_copy(deep=True)
        task.slots['quarter']=3
        task.time_range=TimeRange(start='2026-07-01',end='2026-10-01',end_inclusive=False,label='2026 Q3')
        self.assertIsNone(self.resolve(task,None,self.rag,'calc-test',self.context))
        with self.assertRaises(ValueError):
            self.authorize(self.bundle,task=task)

    def test_monetary_plan_is_not_a_units_plan(self):
        task=self.task.model_copy(update={'business_metric_ids':['units_sold']})
        self.assertIsNone(self.resolve(task,None,self.rag,'calc-test',self.context))
        with self.assertRaises(ValueError):
            self.authorize(self.bundle,task=task)

    def test_month_boundary_and_metric_specific_table_column(self):
        task=self.task.model_copy(deep=True)
        task.slots={'year':2026,'month':12}
        task.time_range=TimeRange(start='2026-12-01',end='2027-01-01',end_inclusive=False,label='2026-12')
        self.assertEqual(task_period(task).end,'2027-01-01')
        chunk=self.chunk.model_copy(update={'page':1,'text':'统计范围\nRock\nGenre:1\n3.25\n4'})
        scope=N(country_ids=[],genre_ids=['Genre:1'])
        self.assertEqual(quarterly_targets([chunk],scope,'units_sold')[1],Decimal('4'))
        self.assertEqual(quarterly_targets([chunk],scope,'sales_amount')[1],Decimal('3.25'))


class MonthScopeTests(unittest.TestCase):
    def test_historical_backend_keeps_missing_year_clarification(self):
        profile=ProfileRegistry.stage1().get('chinook-music')
        memory=SessionMemory(profile_id=profile.profile_id,config_version=profile.config_version)
        parsed=parse_question('第三季度 Rock 销售额是多少？',profile,memory)
        self.assertEqual(parsed.decision,'clarification')
        self.assertIn('year',{item.name for item in parsed.clarification.missing_slots})

    def test_month_dimension_rejects_day_grouping_and_quarter_total(self):
        ctx={'entities':[],'metric_ids':['sales_amount'],'resolved_slots':{'start_date':'2026-04-01',
            'end_date':'2026-07-01','media_ids':[1,2,4,5],'query_intent':{'dimension':'month','measures':['sales_amount']}}}
        query=_controlled_query(ctx,ctx['metric_ids'])
        self.assertTrue(_candidate_covers_context(query,ctx))
        bad={**query,'sql':query['sql'].replace('%Y-%m','%Y-%m-%d')}
        self.assertFalse(_candidate_covers_context(bad,ctx))


if __name__=='__main__':
    unittest.main()
