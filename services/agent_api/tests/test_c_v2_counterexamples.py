import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory, logical_copy
from services.agent_api.app.agent.semantic_shadow import audit_proposal, ENABLED_FOR_EXECUTION
from services.agent_api.app.profiles import ProfileRegistry


class CounterexampleTests(unittest.TestCase):
    def setUp(self):
        self.p = ProfileRegistry.defaults().get("chinook-music")
        self.m = SessionMemory(profile_id=self.p.profile_id, config_version=self.p.config_version)

    def test_doc_identifier_not_month(self):
        parsed = parse_question("2025Q3全音频每月数据库实际销售额与D06月度计划及D08复盘核对", self.p, self.m)
        self.assertNotIn("month", parsed.task.slots)
        self.assertEqual(parsed.task.time_range.start, "2025-07-01")

    def test_explicit_yoy_overrides_previous_quarter(self):
        a = parse_question("2025Q2全音频销售额", self.p, self.m)
        self.m.last_task = a.task
        b = parse_question("换成第三季度", self.p, self.m)
        self.m.last_task = b.task
        c = parse_question("同比增长率是多少？", self.p, self.m)
        self.assertEqual(c.task.slots["comparison_period"], {"year": 2024, "quarter": 3})

    def test_explicit_range_and_invalid_range(self):
        a = parse_question("全部音频2025-07-01至2025-07-31销售额是多少", self.p, self.m)
        self.assertEqual(a.task.time_range.end, "2025-08-01")
        b = parse_question("全部音频2025-09-01至2025-07-01销售额是多少", self.p, self.m)
        self.assertEqual(b.decision, "clarification")
        c = parse_question("全部音频2025-07-01至2025-99-01销售额是多少", self.p, self.m)
        self.assertEqual(c.decision, "clarification")

    def test_unapproved_exclusion_never_silently_executes(self):
        p = parse_question("2025Q3全部音频销售额，排除Metal", self.p, self.m)
        self.assertEqual(p.decision, "unsupported")

    def test_logical_memory_discards_execution_references(self):
        task = parse_question("2025Q3 Rock销售额", self.p, self.m).task
        task.evidence_coverage = [{"complete": True, "source_refs": ["old"]}]
        task.slot_provenance["track_ids"] = {"source": "verified", "source_refs": ["old"]}
        saved = logical_copy(task)
        self.assertEqual(saved.evidence_coverage, [])
        self.assertNotIn("source_refs", saved.slot_provenance["track_ids"])

    def test_shadow_rejects_stale_and_privileged_outputs(self):
        task = parse_question("2025Q3 Rock销售额", self.p, self.m).task
        patch = {"task_id": task.task_id, "expected_version": task.task_version, "action": "correct", "set_slots": {"quarter": 2}}
        valid = {"goal": "更正季度", "patch": patch}
        self.assertTrue(audit_proposal(valid, task)["valid"])
        self.assertFalse(ENABLED_FOR_EXECUTION)
        self.assertEqual(task.slots["quarter"], 3)
        self.assertFalse(audit_proposal({**valid, "sql": "DELETE"}, task)["valid"])
        patch["expected_version"] += 1
        self.assertFalse(audit_proposal(valid, task)["valid"])
