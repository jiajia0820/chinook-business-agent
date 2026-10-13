import json
import unittest
from pathlib import Path

from scripts.run_agent_eval import summarize_file_result


ROOT = Path(__file__).resolve().parents[3]
ROBUSTNESS = ROOT / "eval" / "agent" / "eval_robustness.jsonl"
MANIFEST = ROOT / "eval" / "agent" / "eval_manifest.json"


class RobustnessContractTests(unittest.TestCase):
    def test_manifest_declares_robustness_suite(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("eval_robustness.jsonl", manifest["files"])
        self.assertEqual(manifest["files"]["eval_robustness.jsonl"], 12)

    def test_robustness_cases_have_interface_contract(self):
        cases = [
            json.loads(line)
            for line in ROBUSTNESS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(cases), 12)
        self.assertEqual(len({case["id"] for case in cases}), 12)
        for case in cases:
            with self.subTest(case=case.get("id")):
                expectation = case["interface_expectation"]
                self.assertIn("expected_status", expectation)
                self.assertIn("expected_route", expectation)
                self.assertTrue(case["invariants"])
                self.assertTrue(case["gold_answer_requirements"])

    def test_multiturn_summary_counts_sessions(self):
        summary = summarize_file_result(
            "eval_multiturn.jsonl",
            [{"passed": True}, {"passed": False}],
            [{"passed": True}, {"passed": True}, {"passed": False}],
        )
        self.assertEqual(summary, "  eval_multiturn.jsonl: 2/3 通过")


if __name__ == "__main__":
    unittest.main()
