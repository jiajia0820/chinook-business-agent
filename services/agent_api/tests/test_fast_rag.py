"""Real graph + verified local knowledge; external network is forbidden."""
import unittest
from unittest.mock import patch

from scripts.stage2_local_check import UnavailableModel
from services.agent_api.app.contracts import AskRequest, DocumentChunk
from services.agent_api.app.integrations.sql_d12 import create_canonical_knowledge_integration
from services.agent_api.app.knowledge.source_plan import rag_source_plan
from services.agent_api.app.knowledge.index import KnowledgeIndex


class CountingUnavailableModel(UnavailableModel):
    calls = 0

    def generate_json(self, *args):
        self.calls += 1
        return super().generate_json(*args)


class FastRagIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model = CountingUnavailableModel()
        with patch('backend.sql_module.generation.HTTPJSONModel.from_env', return_value=self.model):
            self.integration = await create_canonical_knowledge_integration(model_mode='live')
            self.service = self.integration.create_formal_graph_service()

    async def asyncTearDown(self):
        await self.integration.aclose()

    async def test_rule_intents_answer_locally_without_model_or_sql(self):
        cases = (
            ('销售额、销量、订单数和购买客户数分别怎么计算？', ('UnitPrice', 'Quantity', 'InvoiceId', 'CustomerId')),
            ('Rock和Rock And Roll在统计上是一个分类吗？', ('Genre:1', 'Genre:5')),
            ('一个月销售额有没有增长，应该和什么时间段比较？', ('月', '比较')),
            ('同一张订单里同时有音频和视频明细，算音乐销售额时应该怎么处理？', ('InvoiceLine', 'Invoice.Total')),
        )
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')) as network:
            for index, (question, facts) in enumerate(cases):
                with self.subTest(question=question):
                    response = await self.service.ask(AskRequest(profile_id='chinook-music', question=question), request_id=f'fast-rag-{index}')
                    self.assertEqual(response.route, 'rag', response.model_dump_json())
                    self.assertEqual(response.status, 'answered', response.model_dump_json())
                    self.assertEqual(response.sql_results, [])
                    for fact in facts:
                        self.assertIn(fact, response.answer)
                    retrievals = [event for event in response.trace if event.tool == 'rag.retrieve']
                    self.assertLessEqual(len(retrievals), 2)
            network.assert_not_called()
        self.assertEqual(self.model.calls, 0)

    async def test_missing_secondary_keeps_grounded_primary_answer(self):
        original = KnowledgeIndex.retrieve

        def only_primary(index, request):
            result = original(index, request)
            return result.model_copy(update={'chunks': [chunk for chunk in result.chunks if chunk.doc_id == 'D01']})

        with patch.object(KnowledgeIndex, 'retrieve', only_primary), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')) as network:
            response = await self.service.ask(AskRequest(profile_id='chinook-music', question='销售额指标定义是什么？'), request_id='primary-only')
            self.assertEqual(response.status, 'answered', response.model_dump_json())
            self.assertEqual({chunk.doc_id for chunk in response.documents}, {'D01'})
            self.assertTrue(any('D02' in note and '补充' in note for note in response.limitations))
            network.assert_not_called()
        self.assertEqual(self.model.calls, 0)

    async def test_flag_off_retains_one_model_fallback_attempt(self):
        with patch.dict('os.environ', {'AGENT_FAST_RAG': '0'}), patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')) as network:
            response = await self.service.ask(AskRequest(profile_id='chinook-music', question='重金属是否并入 Metal？'), request_id='legacy-render')
            self.assertEqual(response.status, 'answered', response.model_dump_json())
            network.assert_not_called()
        self.assertEqual(self.model.calls, 1)

    async def test_only_explicit_source_does_not_add_implicit_sources(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network forbidden')) as network:
            response = await self.service.ask(AskRequest(profile_id='chinook-music', question='仅依据D12说明默认音乐范围'), request_id='only-d12')
            self.assertEqual(response.status, 'answered', response.model_dump_json())
            self.assertEqual({chunk.doc_id for chunk in response.documents}, {'D12'})
            network.assert_not_called()
        self.assertEqual(self.model.calls, 0)


class FastSourcePlanTests(unittest.TestCase):
    def test_fact_grouping_does_not_inherit_document_gate(self):
        from services.agent_api.app.agent.parsing import parse_question
        from services.agent_api.app.agent.state import SessionMemory
        from services.agent_api.app.profiles import ProfileRegistry
        profile = ProfileRegistry.defaults().get('chinook-music')
        for q in ('按月份列出2025年音频销售额和订单数，订单按InvoiceId去重。', '2025年第三季度USA Rock音频销售额、销量和订单数分别是多少？订单去重统计。', '列出2025年第三季度所有媒体类型的销售额和销量，无成交的也显示为0。'):
            result = parse_question(q, profile, SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version))
            self.assertEqual(result.decision, 'ready', result.model_dump_json())
            self.assertEqual(result.task.route, 'sql', result.model_dump_json())

    def test_primary_content_cannot_be_supplied_only_by_secondary(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        docs = [DocumentChunk(doc_id='D01', chunk_id='maintenance', title='指标', doc_type='pdf', text='经营指标清单由维护负责人每月审查。', source_uri='local/D01'),
                DocumentChunk(doc_id='D02', chunk_id='definition', title='方法', doc_type='pdf', text='销售额按SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity)计算。', source_uri='local/D02')]
        self.assertIsNone(local_answer('销售额指标定义是什么？', docs))

    def test_customer_thresholds_require_customer_context(self):
        from services.agent_api.app.knowledge.facts import answer_obligations
        self.assertNotIn('customer_thresholds', answer_obligations('选品时A、B、C、D四类候选分别怎么处理？'))
        self.assertIn('customer_thresholds', answer_obligations('客户A、B、C层阈值是什么？'))

    def test_date_interval_keeps_boundary_convention(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        doc = DocumentChunk(doc_id='D01', chunk_id='date', title='指标', doc_type='pdf', source_uri='local/date', text='时间筛选统一使用左闭右开区间。2028年第三季度写作2028-07-01 <= InvoiceDate < 2028-10-01。')
        answer = local_answer('2028年第三季度订单日期范围怎么写？', [doc])
        self.assertIn('左闭右开', answer)
        self.assertIn('2028-07-01', answer)

    def test_core_review_summary_preserves_change_values(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        doc = DocumentChunk(doc_id='D07', chunk_id='summary', title='复盘', doc_type='pdf', source_uri='local/review', text='一 经营摘要\n二季度音频销售额203.50美元，销量200件，19张订单，18位购买客户。相比一季度增加7.25美元。\n六 数据核对\n二季度音频销售额经业务负责人核对确认，用于经营例会复盘。')
        self.assertIn('7.25', local_answer('二季度音频经营核心结果是什么？', [doc]))

    def test_audio_identifiers_have_explicit_numeric_boundaries(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        doc = DocumentChunk(doc_id='D12', chunk_id='media', title='FAQ', doc_type='md', source_uri='local/media', text='默认只统计音频MediaTypeId 2、6、8。计算音乐销售额按InvoiceLine逐条筛选，不能使用Invoice.Total；视频单独统计。')
        self.assertIsNotNone(local_answer('同一张订单有音频和视频，音乐销售额应该怎么处理？', [doc]))
        answer = local_answer('同一张订单有音频和视频，音乐销售额应该怎么处理？', [doc])
        self.assertIn('2, 6, 8', answer)

    def test_review_comparison_metric_names_keep_document_route(self):
        from services.agent_api.app.agent.route_policy import classify_question
        task_type, route, _ = classify_question('结合二季度和三季度复盘，销售额、订单数和购买客户数发生了什么变化？', ['sales_amount', 'order_count', 'purchasing_customers'])
        self.assertEqual((task_type, route), ('document_rule', 'rag'))

    def test_definition_secondary_source_is_not_a_blocker(self):
        plan = rag_source_plan('销售额指标定义是什么？')
        self.assertEqual(plan.primary_docs, ['D01'])
        self.assertEqual(plan.secondary_docs, ['D02'])

    def test_explicit_only_source_is_preserved(self):
        plan = rag_source_plan('仅依据D12说明音乐销售额口径')
        self.assertEqual(plan.primary_docs, ['D12'])
        self.assertEqual(plan.required_docs, ['D12'])

    def test_comparison_method_uses_method_source_not_quarter_reviews(self):
        plan = rag_source_plan('一个月销售额有没有增长，应该和什么时间段比较？')
        self.assertEqual(plan.primary_docs, ['D02'])
        self.assertNotIn('D07', plan.required_docs)
        self.assertNotIn('D08', plan.required_docs)

    def test_table_renderer_preserves_numeric_cell_boundaries(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        chunk = DocumentChunk(doc_id='D06', chunk_id='table', title='计划', doc_type='pdf',
                              text='| 统计范围 | 销售额目标 | 销量目标 |\n| Rock | 50.00 | 60 |', source_uri='knowledge/table')
        answer = local_answer('Rock销售额目标和销量目标是什么？', [chunk])
        self.assertIn('| Rock | 50.00 | 60 |', answer)
        self.assertNotIn('50.0060', answer)

    def test_doc_id_does_not_supply_missing_content(self):
        from services.agent_api.app.response.fast_renderer import local_answer
        chunk = DocumentChunk(doc_id='D01', chunk_id='wrong', title='指标', doc_type='pdf',
                              text='年度指标清单需要负责人审核。', source_uri='knowledge/wrong')
        self.assertIsNone(local_answer('销售额、销量、订单数和购买客户数分别怎么计算？', [chunk]))
