import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.run_four_task_scorecard import score_report, ScorecardError


class FourTaskScorecardTests(unittest.TestCase):
    def test_supports_old_passed_and_new_business_pending_shape(self):
        report = {
            "total": 4,
            "passed": 2,
            "results": [
                {"id": "entity-rewrite-001", "passed": True, "problems": []},
                {"id": "formula-001", "business_passed": True, "pending_external_api": True, "problems": []},
                {"id": "clarification-001", "business_passed": False, "problems": ["未提出澄清"]},
                {"id": "multiturn-001", "passed": True, "pending_external_api": False, "problems": []},
            ],
        }
        scored = score_report(report)
        self.assertEqual(scored["total"], 4)
        self.assertEqual(scored["business_passed"], 3)
        self.assertEqual(scored["pending_external_api"], 1)
        self.assertEqual(scored["overall"], 2)
        self.assertEqual(scored["by_suite"]["clarification"]["total"], 1)
        self.assertEqual(scored["by_suite"]["multiturn"]["total"], 1)
        self.assertEqual(scored["by_suite"]["formula"]["pending_external_api"], 1)
        self.assertTrue(any(x["id"] == "clarification-001" for x in scored["failures"]))
        self.assertTrue(any(x["id"] == "formula-001" and x["reason"] == "pending_external_api" for x in scored["failures"]))

    def test_missing_business_and_old_passed_fields_is_failure(self):
        report = {"total": 1, "results": [{"id": "regression-001", "problems": []}]}
        scored = score_report(report)
        self.assertEqual(scored["total"], 1)
        self.assertEqual(scored["business_passed"], 0)
        self.assertEqual(scored["overall"], 0)
        self.assertEqual(scored["failures"][0]["reason"], "missing_business_status")

    def test_rejects_total_mismatch_and_duplicate_ids(self):
        with self.assertRaises(ScorecardError):
            score_report({"total": 2, "results": [{"id": "a", "passed": True}]})
        with self.assertRaises(ScorecardError):
            score_report({"total": 2, "results": [{"id": "a", "passed": True}, {"id": "a", "passed": False}]})

    def test_real_eval_prefix_aliases_keep_clarification_and_multiturn_separate(self):
        ids = [
            ("special-alias-001", "entity_rewrite"),
            ("eval_formula-001", "formula"),
            ("eval_cross_source-001", "cross_source"),
            ("eval_clarification-001", "clarification"),
            ("session-001", "multiturn"),
            ("eval_regression-001", "regression"),
        ]
        report = {"total": len(ids), "results": [{"id": key, "passed": True} for key, _ in ids]}
        scored = score_report(report)
        self.assertEqual([scored["by_suite"][suite]["total"] for _, suite in ids], [1] * len(ids))
        self.assertEqual(set(scored["by_suite"]), {suite for _, suite in ids})

    def test_cli_writes_json_report(self):
        report = {"total": 2, "results": [
            {"id": "cross-source-001", "passed": True, "problems": []},
            {"id": "clarification-001", "passed": False, "problems": ["x"]},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "report.json"
            out = Path(tmp) / "scorecard.json"
            src.write_text(json.dumps(report), encoding="utf-8")
            proc = subprocess.run([sys.executable, "scripts/run_four_task_scorecard.py", "--report", str(src), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["total"], 2)
            self.assertEqual(payload["by_suite"]["cross_source"]["total"], 1)
            self.assertEqual(payload["by_suite"]["clarification"]["business_passed"], 0)


if __name__ == "__main__":
    unittest.main()
