"""Real D06 tables and named formula evidence must survive strict binding."""
from decimal import Decimal
from types import SimpleNamespace
import unittest

from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.contracts import DocumentChunk, RetrievalResponse, SqlQueryResponse, SqlSource, TimeRange
from services.agent_api.app.evidence.binding import authorize_calculation
from services.agent_api.app.evidence.cross_source_bundle import build_cross_source_bundle
from services.agent_api.app.evidence.models import EvidenceScope
from services.agent_api.app.integrations.business_calculation import create_business_resolver, locator, monthly_plan, quarterly_targets
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.local_calculator import calculate_decimal
from services.agent_api.app.tools.models import SqlTaskOutcome
from services.agent_api.app.tools.references import sql_cell_ref


class FormulaSourceRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Minimal source excerpts preserve D06's actual row/column layout.
        # No untracked index snapshot or expensive OCR is needed by the tests.
        sources = [
            ("D01", "D01-source", 1, "客单价（average_order_value）等于同一筛选范围内的销售额除以订单数。\n"
                "环比（period_over_period_growth）的计算公式为（本期销售额 - 上期销售额）/上期销售额。"),
            ("D06", "D06-source#P1:T0", 1, "附件1 季度目标明细及口径\n统计范围\n分类编号\n销售额目标 美元\n销量目标 件\n"
                "Rock\nGenre:1\n50.00\n50\n主要增长品类\nMetal\nGenre:3\n24.00\n25\n保持基础贡献\n"
                "Latin\nGenre:7\n18.00\n18\n稳定经营\n全部音频\n全部音频分类\n120.00\n125\n季度整体经营目标"),
            ("D06", "D06-source#P2:T0", 2, "附件2 月度检查安排\n一 月度分解\n统计范围\n7月金额 美元\n8月金额 美元\n"
                "9月金额 美元\n季度金额 美元\nRock\n16.00\n16.00\n18.00\n50.00\nMetal\n8.00\n8.00\n8.00\n24.00\n"
                "Latin\n6.00\n6.00\n6.00\n18.00\n全部音频\n38.00\n40.00\n42.00\n120.00"),
            ("D06", "D06-source#P2:T1", 2, "统计范围\n7月销量 件\n8月销量 件\n9月销量 件\n季度销量 件\n"
                "Rock\n16\n16\n18\n50\nMetal\n8\n8\n9\n25\nLatin\n6\n6\n6\n18\n全部音频\n40\n41\n44\n125"),
            ("D06", "D06-source#P4", 4, "二 计算与结论\n销售额差额＝实际销售额－销售额目标。"
                "销售额达成率＝实际销售额÷销售额目标×100%。"),
        ]
        cls.real_chunks = [DocumentChunk(chunk_id=chunk_id, doc_id=doc_id,
            title="2025Q3经营目标表" if doc_id == "D06" else "指标与计算公式", doc_type="pdf", page=page,
            text=text, source_uri=f"data/knowledge/{doc_id}.pdf#P{page}", retrieval_id="r-source")
            for doc_id, chunk_id, page, text in sources]

    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.rag = RetrievalResponse(retrieval_id="r-source", profile_id=self.profile.profile_id,
            status="success", chunks=self.real_chunks)
        self.resolve = create_business_resolver(SimpleNamespace(profile=self.profile,
            documents={"D06": SimpleNamespace(title="2025Q3经营目标表")}))

    def task(self, *, intent="target_attainment", metric="sales_amount", genre=None, month=None):
        slots = {"year": 2025, "media_ids": [1, 2, 4, 5]}
        slots.update({"month": month} if month else {"quarter": 3})
        start, end = (f"2025-{month:02}-01", f"2025-{month + 1:02}-01") if month else ("2025-07-01", "2025-10-01")
        return BusinessTask(original_question="实际与目标比较", normalized_question="实际与目标比较",
            intent=intent, route="cross_source", needs_calculation=True, business_metric_ids=[metric],
            slots=slots, entities=[] if genre is None else [{"type": "genre", "id": genre, "label": genre}],
            time_range=TimeRange(start=start, end=end, end_inclusive=False, label=start))

    def sql(self, metric="sales_amount", value="112.86", *, orders=None):
        row = {metric: value}
        if orders is not None:
            row["order_count"] = orders
        result = SqlQueryResponse(query_id="q-source", profile_id=self.profile.profile_id, status="success",
            columns=list(row), rows=[row], row_count=1, truncated=False, execution_ms=1,
            source=SqlSource(type="database", name="chinook", tables=["Invoice", "InvoiceLine", "Track"]))
        return SqlTaskOutcome(query_id="task-source", profile_id=self.profile.profile_id, status="success", sql_results=[result])

    def context(self, rag, sql=None):
        refs = [c.chunk_id for c in rag.chunks]
        if sql:
            refs.extend(sql_cell_ref(r.query_id, i, col) for r in sql.sql_results for i in range(len(r.rows)) for col in r.columns)
        return ToolContext(request_id="req-source", session_id="sess-source", profile_id=self.profile.profile_id,
            available_source_refs=tuple(refs))

    def authorize(self, bundle, task, sql, rag=None):
        rag = rag or self.rag
        return authorize_calculation(bundle, task=task, sql=sql, rag=rag, profile=self.profile,
            context=self.context(rag, sql), calculation_id="calc-source", expected_mode="verified_business")

    def bundle(self, task, sql=None, rag=None):
        rag = rag or self.rag
        return self.resolve(task, sql, rag, "calc-source", self.context(rag, sql))

    def test_real_quarter_total_uses_canonical_entity_and_exact_span(self):
        task, sql = self.task(), self.sql()
        bundle = self.bundle(task, sql)
        self.assertIsNotNone(bundle)
        target = bundle.operands["target"]
        self.assertEqual(target.value, Decimal("120.00"))
        chunk = next(c for c in self.rag.chunks if c.chunk_id == target.locator.chunk_id)
        self.assertEqual(chunk.text[target.locator.start:target.locator.end], "120.00")
        self.assertIn("全部音频分类", chunk.text)
        self.assertEqual(calculate_decimal(self.authorize(bundle, task, sql).request), Decimal("94.05"))

    def test_real_monthly_targets_bind_month_without_inventing_quarter_slot(self):
        for month, genre, metric, expected in [(7, None, "sales_amount", "38.00"),
                (8, "Genre:1", "sales_amount", "16.00"), (9, "Genre:3", "units_sold", "9")]:
            with self.subTest(month=month, genre=genre, metric=metric):
                task, sql = self.task(month=month, genre=genre, metric=metric), self.sql(metric, "8")
                bundle = self.bundle(task, sql)
                self.assertIsNotNone(bundle)
                self.assertEqual(bundle.operands["target"].value, Decimal(expected))
                self.authorize(bundle, task, sql)
                self.assertNotIn("quarter", task.slots)

    def test_monthly_parser_selects_metric_table_and_exact_source_spans(self):
        scope = EvidenceScope(media_ids=[1, 2, 4, 5], genre_ids=["Genre:3"])
        for metric, expected in [("sales_amount", ["8.00", "8.00", "8.00"]), ("units_sold", ["8", "8", "9"])]:
            rows = monthly_plan(self.rag.chunks, scope, metric)
            self.assertEqual([month for month, *_ in rows], [7, 8, 9])
            self.assertEqual([value for _, value, *_ in rows], list(map(Decimal, expected)))
            self.assertEqual([c.text[start:end] for _, _, c, start, end in rows], expected)

    def test_real_monthly_sum_keeps_three_month_spans(self):
        task = self.task(intent="document_sum")
        bundle = self.bundle(task)
        self.assertIsNotNone(bundle)
        self.assertEqual(set(bundle.operands), {"month_2025_07", "month_2025_08", "month_2025_09"})
        self.assertEqual(calculate_decimal(self.authorize(bundle, task, None).request), Decimal("120.00"))
        for role, expected in [("month_2025_07", "38.00"), ("month_2025_08", "40.00"), ("month_2025_09", "42.00")]:
            evidence = bundle.operands[role]
            chunk = next(c for c in self.rag.chunks if c.chunk_id == evidence.locator.chunk_id)
            self.assertEqual(chunk.text[evidence.locator.start:evidence.locator.end], expected)

    def test_real_numeric_target_table_without_formula_is_rejected(self):
        rag = self.rag.model_copy(update={"chunks": [c for c in self.rag.chunks if c.doc_id == "D06" and c.page == 1]})
        self.assertIsNone(self.bundle(self.task(genre="Genre:1"), self.sql(), rag))
        with self.assertRaisesRegex(ValueError, "formula"):
            build_cross_source_bundle(self.task(genre="Genre:1"), self.sql(), rag)

    def test_authorized_doc_id_alone_does_not_authorize_formula(self):
        task, sql = self.task(genre="Genre:1"), self.sql()
        bundle = self.bundle(task, sql)
        table = next(c for c in self.rag.chunks if c.doc_id == "D06" and "#P1:T0" in c.chunk_id)
        bundle.formula.locator = locator(table)
        with self.assertRaisesRegex(ValueError, "named function"):
            self.authorize(bundle, task, sql)

    def test_named_formula_in_unapproved_doc_is_rejected(self):
        task, sql = self.task(genre="Genre:1"), self.sql()
        bundle = self.bundle(task, sql)
        chunk = self.rag.chunks[0].model_copy(update={"chunk_id": "D02-unapproved-attainment", "doc_id": "D02",
            "text": "销售额达成率＝实际销售额÷目标销售额×100%"})
        rag = self.rag.model_copy(update={"chunks": [*self.rag.chunks, chunk]})
        bundle.formula.locator = locator(chunk)
        with self.assertRaisesRegex(ValueError, "registry-authorized"):
            self.authorize(bundle, task, sql, rag)

    def test_unlisted_metric_or_scope_cannot_inherit_target_column(self):
        scope = EvidenceScope(media_ids=[1, 2, 4, 5])
        self.assertIsNone(quarterly_targets(self.rag.chunks, scope, "order_count"))
        self.assertEqual(monthly_plan(self.rag.chunks, scope, "order_count"), [])
        self.assertEqual(monthly_plan(self.rag.chunks, scope.model_copy(update={"genre_ids": ["Genre:13"]}), "sales_amount"), [])
        self.assertIsNone(self.bundle(self.task(month=6), self.sql()))

    def test_monthly_monetary_sum_is_not_units_sum(self):
        self.assertIsNone(self.bundle(self.task(intent="document_sum", metric="units_sold")))

    def test_average_order_value_uses_sql_sales_and_orders_and_result_unit(self):
        task, sql = self.task(intent="average_order_value", metric="average_order_value"), self.sql(value="108.90", orders="19")
        bundle = self.bundle(task, sql)
        self.assertIsNotNone(bundle)
        self.assertEqual(bundle.formula.result_unit, "USD/单")
        self.assertEqual(calculate_decimal(self.authorize(bundle, task, sql).request), Decimal("5.73"))

    def test_monthly_sum_rejects_foreign_currency_table(self):
        rag = self.rag.model_copy(update={"chunks": [c.model_copy(update={"text": c.text.replace("金额 美元", "金额 人民币")})
            for c in self.rag.chunks]})
        self.assertIsNone(self.bundle(self.task(intent="document_sum"), rag=rag))

    def test_sql_wrong_metric_column_is_not_rebound_as_sales(self):
        self.assertIsNone(self.bundle(self.task(), self.sql(metric="units_sold", value="10")))
        task, sql = self.task(), self.sql(value="10", orders="10")
        bundle = self.bundle(task, sql)
        bundle.operands["actual"].column = "order_count"
        with self.assertRaisesRegex(ValueError, "SQL metric"):
            self.authorize(bundle, task, sql)

    def test_period_difference_and_growth_retain_sql_periods(self):
        for intent, expected in [("period_difference", "3.96"), ("growth_rate", "3.64")]:
            with self.subTest(intent=intent):
                task, sql = self.task(intent=intent), self.sql()
                task.slots["comparison_period"] = {"year": 2025, "quarter": 2}
                previous = sql.sql_results[0].model_copy(update={"query_id": "q-previous", "rows": [{"sales_amount": "108.90"}]})
                sql.sql_results.append(previous)
                bundle = self.bundle(task, sql)
                prior_role = "previous" if intent == "growth_rate" else "target"
                self.assertEqual(bundle.operands[prior_role].kind, "sql")
                self.assertEqual(bundle.operands[prior_role].period.start, "2025-04-01")
                self.assertEqual(calculate_decimal(self.authorize(bundle, task, sql).request), Decimal(expected))

    def test_document_actual_or_mismatched_scope_remains_rejected(self):
        task, sql = self.task(genre="Genre:1"), self.sql()
        bundle = self.bundle(task, sql)
        bundle.operands["actual"] = bundle.operands["target"].model_copy(deep=True)
        with self.assertRaisesRegex(ValueError, "require SQL"):
            self.authorize(bundle, task, sql)
        bundle = self.bundle(task, sql)
        bundle.operands["target"].scope = bundle.operands["target"].scope.model_copy(update={"genre_ids": ["Genre:3"]})
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            self.authorize(bundle, task, sql)

    def test_target_binding_filters_other_quarter_sources(self):
        foreign = [c.model_copy(update={"chunk_id": c.chunk_id + "-other-year", "title": "2026Q3经营目标表"})
            for c in self.rag.chunks if c.doc_id == "D06"]
        rag = self.rag.model_copy(update={"chunks": [*foreign, *self.rag.chunks]})
        for task, sql in [(self.task(), self.sql()), (self.task(month=7), self.sql()), (self.task(intent="document_sum"), None)]:
            with self.subTest(intent=task.intent, slots=task.slots):
                self.authorize(self.bundle(task, sql, rag), task, sql, rag)

    def test_equal_month_values_still_require_correct_role_span(self):
        task = self.task(intent="document_sum", genre="Genre:1")
        bundle = self.bundle(task)
        self.assertEqual(bundle.operands["month_2025_07"].value, bundle.operands["month_2025_08"].value)
        bundle.operands["month_2025_07"], bundle.operands["month_2025_08"] = bundle.operands["month_2025_08"], bundle.operands["month_2025_07"]
        with self.assertRaisesRegex(ValueError, "entity/metric/column"):
            self.authorize(bundle, task, None)


if __name__ == "__main__":
    unittest.main()
