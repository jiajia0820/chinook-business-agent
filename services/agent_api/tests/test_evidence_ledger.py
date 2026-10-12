import unittest
from decimal import Decimal

from services.agent_api.app.evidence.ledger import EvidenceItem, EvidenceLedger
from services.agent_api.app.evidence.models import EvidencePeriod, EvidenceScope, ScalarSemantics
from services.agent_api.app.evidence.verification import verify_same_scope
from services.agent_api.app.evidence.binding import _verify_cross_source_scope
from services.agent_api.app.evidence.models import DocumentLocator, DocumentScalarEvidence, SqlScalarEvidence


class EvidenceLedgerTests(unittest.TestCase):
    def period(self, start="2025-07-01", end="2025-10-01"):
        return EvidencePeriod(start=start, end=end)

    def scope(self, genre="Genre:3"):
        return EvidenceScope(media_ids=[1, 2, 4, 5], genre_ids=[genre])

    def item(self, role="actual", *, period=None, scope=None, metric="sales_amount", unit="USD", value="21.78", status="verified", source_kind=None):
        return EvidenceItem(
            fact_role=role,
            metric=metric,
            value=Decimal(value),
            unit=unit,
            period=period or self.period(),
            scope=scope or self.scope(),
            source_ref=f"{role}-source",
            source_kind=source_kind or ("sql" if role in {"actual", "current", "previous"} else "document"),
            validation_status=status,
        )

    def test_ledger_required_returns_the_single_verified_role(self):
        ledger = EvidenceLedger(items=[self.item("actual"), self.item("target", value="24.0", source_kind="document")])
        self.assertEqual(ledger.required("actual").value, Decimal("21.78"))
        with self.assertRaises(ValueError):
            EvidenceLedger(items=[self.item("actual", status="unverified")]).required("actual")

    def test_scalar_semantics_is_accepted_as_the_canonical_dimensions(self):
        semantics = ScalarSemantics(
            metric_id="sales_amount",
            period=self.period(),
            scope=self.scope(),
            unit="USD",
            value=Decimal("21.78"),
            confirmed=True,
        )
        item = EvidenceItem(fact_role="actual", semantics=semantics, source_ref="sql-1", source_kind="sql", validation_status="verified")
        self.assertEqual(item.metric, "sales_amount")
        self.assertEqual(item.period, semantics.period)
        self.assertEqual(item.scope, semantics.scope)
        self.assertEqual(item.unit, "USD")

    def test_actual_and_target_require_same_period_scope_metric_and_unit(self):
        actual = self.item("actual")
        target = self.item("target", value="24.0", source_kind="document")
        self.assertTrue(verify_same_scope(actual, target))
        self.assertFalse(verify_same_scope(actual, self.item("target", period=self.period("2025-04-01", "2025-07-01"), value="24.0", source_kind="document")))
        self.assertFalse(verify_same_scope(actual, self.item("target", scope=self.scope("Genre:13"), value="24.0", source_kind="document")))
        self.assertFalse(verify_same_scope(actual, self.item("target", metric="units_sold", value="24.0", source_kind="document")))
        self.assertFalse(verify_same_scope(actual, self.item("target", unit="件", value="24.0", source_kind="document")))

    def test_growth_current_and_previous_may_use_different_periods(self):
        current = self.item("current", period=self.period("2025-07-01", "2025-10-01"))
        previous = self.item("previous", period=self.period("2025-04-01", "2025-07-01"), value="19.50")
        self.assertTrue(verify_same_scope(current, previous))

    def test_growth_still_requires_same_metric_scope_and_unit(self):
        current = self.item("current")
        previous = self.item("previous", period=self.period("2025-04-01", "2025-07-01"), value="19.50")
        self.assertFalse(verify_same_scope(current, self.item("previous", period=previous.period, metric="units_sold", value="19.50")))
        self.assertFalse(verify_same_scope(current, self.item("previous", period=previous.period, scope=self.scope("Genre:13"), value="19.50")))
        self.assertFalse(verify_same_scope(current, self.item("previous", period=previous.period, unit="件", value="19.50")))

    def test_cross_source_binding_rejects_mismatched_operand_scope(self):
        sql = SqlScalarEvidence(
            metric_id="sales_amount", period=self.period(), scope=self.scope(), unit="USD",
            value=Decimal("21.78"), confirmed=True, query_id="q1", row_index=0,
            column="sales_amount", result_sha256="0" * 64,
        )
        locator = DocumentLocator(
            chunk_id="D06#P1", doc_id="D06", version="V1.0", content_sha256="0" * 64,
            source_uri="data/knowledge/D06.pdf#P1", start=0, end=4,
        )
        target = DocumentScalarEvidence(
            metric_id="sales_amount", period=self.period(), scope=self.scope("Genre:13"), unit="USD",
            value=Decimal("24.00"), confirmed=True, locator=locator,
        )
        bundle = type("Bundle", (), {"function": "attainment_rate", "operands": {"actual": sql, "target": target}})()
        with self.assertRaisesRegex(ValueError, "cross-source evidence scope mismatch"):
            _verify_cross_source_scope(bundle)


if __name__ == "__main__":
    unittest.main()

