import unittest

from scripts.diagnose_c_eval import audit_response, normalized_slots


class DiagnosticsTests(unittest.TestCase):
    def test_composite_period_does_not_absorb_metric_or_scope(self):
        self.assertEqual(normalized_slots(["year", "quarter", "scope"]), ["period", "scope"])
        self.assertEqual(normalized_slots(["entity", "classification_dimension"]), ["classification_dimension", "entity"])

    def test_nonempty_source_not_materials(self):
        r = {"request_id": "sample", "request": {"question": "报名要交什么？"}, "response": {"status": "answered", "route": "rag", "answer": "D11：报名很简单"}}
        self.assertEqual(audit_response(r)["findings"][0]["fact"], "submission_materials")

    def test_expected_clarification_is_not_execution_error(self):
        r = {"request_id": "sample", "request": {"question": "销售额？"}, "response": {"status": "clarification_required", "clarification": {"missing_slots": [{"name": "year"}]}}}
        self.assertEqual(audit_response(r)["findings"], [{"kind": "clarification", "slots": ["year"]}])
