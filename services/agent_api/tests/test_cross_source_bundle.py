from decimal import Decimal
import unittest

from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.contracts import DocumentChunk, RetrievalResponse, SqlQueryResponse, SqlSource, TimeRange
from services.agent_api.app.tools.models import SqlTaskOutcome
from services.agent_api.app.evidence.cross_source_bundle import build_cross_source_bundle, build_cross_source_result
from services.agent_api.app.evidence.binding import authorize_calculation
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.runtime import ToolContext
from services.agent_api.app.tools.references import sql_cell_ref


class CrossSourceBundleTests(unittest.TestCase):
    def task(self, intent="target_attainment", metric="sales_amount"):
        return BusinessTask(
            original_question="2025年第三季度Rock销售额达到目标了吗？",
            normalized_question="2025年第三季度Rock销售额达到目标了吗？",
            retrieval_question="2025年第三季度Rock销售额达到目标了吗？",
            intent=intent, route="cross_source", needs_calculation=True,
            business_metric_ids=[metric],
            slots={"year": 2025, "quarter": 3, "media_ids": [1, 2, 4, 5]},
            entities=[{"type": "genre", "id": "Genre:1", "label": "Rock"}],
            time_range=TimeRange(start="2025-07-01", end="2025-10-01", end_inclusive=False, label="2025 Q3"),
        )

    def sql(self, value=47.52):
        result = SqlQueryResponse(
            query_id="q-actual", profile_id="chinook-music", status="success",
            columns=["sales_amount"], rows=[{"sales_amount": value}], row_count=1,
            truncated=False, execution_ms=1,
            source=SqlSource(type="database", name="chinook", tables=["Invoice", "InvoiceLine", "Track", "Genre"]),
        )
        return SqlTaskOutcome(query_id="task-actual", profile_id="chinook-music", status="success", sql_results=[result])

    def docs(self, *, operation=True):
        chunks = [
            DocumentChunk(chunk_id="D06#P1", doc_id="D06", title="2025Q3经营目标表", doc_type="pdf", page=1,
                text="附件1 目标明细\nRock\nGenre:1\n50.00\n50\n全部音频\n120.00\n125", source_uri="data/knowledge/D06.pdf#P1", retrieval_id="r1"),
            DocumentChunk(chunk_id="D01#P1", doc_id="D01", title="指标定义与公式", doc_type="pdf", page=1,
                text="销售额达成率 = 实际销售额 / 目标销售额 × 100%\n销售额差额 = 实际销售额 - 目标销售额", source_uri="data/knowledge/D01.pdf#P1", retrieval_id="r1"),
            DocumentChunk(chunk_id="D08#P2", doc_id="D08", title="Q3经营复盘", doc_type="pdf", page=2,
                text="三 目标达成情况\nRock未达目标。", source_uri="data/knowledge/D08.pdf#P2", retrieval_id="r1"),
        ]
        if operation:
            chunks.append(DocumentChunk(chunk_id="D09#Rock", doc_id="D09", title="Rock品类运营", doc_type="pdf", page=1,
                text="Rock建议继续整理成交商品和专辑，关注专题陈列。", source_uri="data/knowledge/D09.pdf#P1", retrieval_id="r1"))
        return RetrievalResponse(retrieval_id="r1", profile_id="chinook-music", status="success", chunks=chunks)

    def test_q3_rock_bundle_contains_sql_actual_d06_target_and_formula(self):
        bundle = build_cross_source_bundle(self.task(), self.sql(), self.docs())
        self.assertEqual(bundle.function, "attainment_rate")
        self.assertEqual(set(bundle.operands), {"actual", "target"})
        self.assertTrue(bundle.operands["actual"].source_ref.startswith("sql:"))
        self.assertTrue(bundle.operands["target"].source_ref.startswith("D06:"))
        self.assertEqual(bundle.formula.locator.doc_id, "D01")

    def test_q3_metal_bundle_keeps_operation_evidence_separate(self):
        task = self.task()
        result = build_cross_source_result(self.task(intent="target_difference"), self.sql(21.78), self.docs())
        self.assertEqual(result.actual, Decimal("21.78"))
        self.assertEqual(result.target, Decimal("50.00"))
        self.assertEqual(result.gap, Decimal("-28.22"))
        self.assertTrue(result.operation_text_contains("专题"))
        self.assertNotIn("operation", result.calculator_operands)

    def test_missing_operation_does_not_delete_actual_target_calculation(self):
        result = build_cross_source_result(self.task(intent="target_difference"), self.sql(47.52), self.docs(operation=False))
        self.assertEqual(result.gap, Decimal("-2.48"))
        self.assertEqual(result.calculation.result, Decimal("-2.48"))
        self.assertEqual(result.missing_roles, ["operation"])

    def test_missing_actual_or_target_cannot_conclude(self):
        with self.assertRaises(ValueError):
            build_cross_source_result(self.task(), None, self.docs())

    def test_bundle_is_accepted_by_evidence_authorization(self):
        task = self.task()
        sql = self.sql()
        rag = self.docs()
        bundle = build_cross_source_bundle(task, sql, rag, calculation_id="calc-authorized",
            request_id="req", session_id="sess")
        refs = [c.chunk_id for c in rag.chunks]
        refs.append(sql_cell_ref("q-actual", 0, "sales_amount"))
        authorized = authorize_calculation(bundle, task=task, sql=sql, rag=rag,
            profile=ProfileRegistry.defaults().get("chinook-music"),
            context=ToolContext(request_id="req", session_id="sess", profile_id="chinook-music", available_source_refs=tuple(refs)),
            calculation_id="calc-authorized", expected_mode="verified_business")
        self.assertEqual(set(authorized.request.operands), {"actual", "target"})


if __name__ == "__main__":
    unittest.main()
