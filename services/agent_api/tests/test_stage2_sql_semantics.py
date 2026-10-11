import json
import sqlite3
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.sql_module.errors import ModuleError
from backend.sql_module.query_intent import parse_query_intent, validate_intent
from backend.sql_module.service import _controlled_query, _candidate_covers_context
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.c_offline import create_canonical_sql_integration

ROOT = Path(__file__).resolve().parents[3]


def context(metrics, *, top_n=None):
    slots = {'start_date': '2025-07-01', 'end_date': '2025-10-01', 'media_ids': [1,2,4,5]}
    if top_n:
        slots['query_intent'] = {'dimension': 'billing_country', 'top_n': top_n, 'order_by': 'sales_amount', 'order_desc': True}
    return {'resolved_slots': slots, 'metric_ids': metrics, 'entities': []}


class SemanticScopeTests(unittest.TestCase):
    def test_outer_join_date_cannot_filter_preserved_lines(self):
        ctx = context(['media_type_sales', 'sales_amount', 'units_sold'])
        candidate = {'sql': 'SELECT mt.MediaTypeId, mt.Name, SUM(il.UnitPrice*il.Quantity) AS sales_amount, SUM(il.Quantity) AS units_sold FROM main.MediaType mt LEFT JOIN main.Track t ON t.MediaTypeId=mt.MediaTypeId LEFT JOIN main.InvoiceLine il ON il.TrackId=t.TrackId LEFT JOIN main.Invoice i ON i.InvoiceId=il.InvoiceId AND i.InvoiceDate>=:start_date AND i.InvoiceDate<:end_date GROUP BY mt.MediaTypeId, mt.Name', 'params': {'start_date': '2025-07-01', 'end_date': '2025-10-01'}}
        self.assertFalse(_candidate_covers_context(candidate, ctx))
        self.assertTrue(_candidate_covers_context(_controlled_query(ctx, ctx['metric_ids']), ctx))

    def test_bound_dates_with_wrong_operator_or_or_clause_fail(self):
        ctx = context(['sales_amount'])
        good = _controlled_query(ctx, ctx['metric_ids'])
        for wrong in [good['sql'].replace('< :end_date', '<= :end_date'), good['sql'].replace(' AND t.MediaTypeId', ' OR t.MediaTypeId')]:
            self.assertFalse(_candidate_covers_context({**good, 'sql': wrong}, ctx))

    def test_top_n_survives_absent_model_candidate_and_varies(self):
        for size in (3,5,7):
            ctx = context(['billing_country_sales', 'sales_amount'], top_n=size)
            query = _controlled_query(ctx, ctx['metric_ids'])
            with sqlite3.connect(f"{(ROOT/'data/chinook/Chinook.db').as_uri()}?mode=ro", uri=True) as db:
                rows = db.execute(query['sql'], query['params']).fetchall()
            self.assertEqual(len(rows), size)
            self.assertEqual([r[-1] for r in rows], sorted([r[-1] for r in rows], reverse=True))
            self.assertTrue(_candidate_covers_context(query, ctx))

    def test_shared_intent_is_structured_and_bounded(self):
        intent = parse_query_intent('第三季度销售额最高的七个账单国家', ['billing_country_sales'])
        self.assertEqual(intent['top_n'], 7)
        self.assertEqual(intent['dimension'], 'billing_country')
        self.assertFalse(validate_intent({'top_n': True}))
        self.assertFalse(validate_intent({'order_by': 'DROP TABLE Invoice'}))

    def test_comparison_checks_current_and_previous_separately(self):
        ctx = context(['sales_amount'])
        ctx['resolved_slots']['comparison_period'] = {'year': 2025, 'quarter': 2}
        query = _controlled_query(ctx, ctx['metric_ids'])
        self.assertTrue(_candidate_covers_context(query, ctx))
        query['params']['previous_start_date'] = '2025-01-01'
        self.assertFalse(_candidate_covers_context(query, ctx))


class LiveFallbackContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_failure_keeps_top_n_through_b_c_and_composer(self):
        class UnavailableModel:
            def generate_json(self, *args):
                raise ModuleError('SQL_EXECUTION_FAILED', 'test timeout', 'MODEL_TIMEOUT')
        with patch('backend.sql_module.generation.HTTPJSONModel.from_env', return_value=UnavailableModel()):
            integration = await create_canonical_sql_integration(model_mode='live')
        try:
            result = await integration.create_formal_graph_service().ask(AskRequest(
                profile_id='chinook-music', question='2025年第三季度音频销售额最高的五个账单国家是什么？'), request_id='test-fallback')
            self.assertEqual(result.status, 'answered')
            self.assertEqual(len(result.sql_results[0].rows), 5)
            self.assertEqual(result.sql_results[0].rows[0], {'billing_country': 'Brazil', 'sales_amount': 28.71})
            self.assertTrue(any('受控查询' in limitation for limitation in result.limitations))
            self.assertIn('受控查询计划', result.answer)
            self.assertNotIn('大模型生成 SQL；', result.answer)
        finally:
            await integration.aclose()


if __name__ == '__main__':
    unittest.main()
