import unittest
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.p = ProfileRegistry.defaults().get("chinook-music")
        self.m = SessionMemory(profile_id=self.p.profile_id, config_version=self.p.config_version)

    def turn(self, text):
        r = parse_question(text, self.p, self.m)
        self.m.pending, self.m.last_task = (r.task, None) if r.decision == "clarification" else (None, r.task)
        return r

    def test_switch_then_growth_three_paraphrases(self):
        for q in ("增长率是多少？", "增长率呢？", "刚才两期增长了多少？"):
            self.setUp()
            self.turn("2025年第二季度全音频销售额是多少？")
            self.turn("换成第三季度。")
            r = self.turn(q)
            self.assertEqual(r.decision, "ready")
            self.assertEqual(r.task.slots["comparison_period"], {"year": 2025, "quarter": 2})
            self.assertEqual(r.task.business_metric_ids, ["sales_amount"])

    def test_unconfirmed_year_remains_only_missing_year(self):
        self.turn("第二季度音频销售额是多少？")
        self.turn("换成第三季度。")
        r = self.turn("增长率是多少？")
        self.assertEqual([s.name for s in r.clarification.missing_slots], ["year"])

    def test_year_only_reply_not_annual_confirmation(self):
        self.turn("Rock销售额是多少？")
        r = self.turn("2025")
        self.assertEqual(r.decision, "clarification")
        self.assertIsNone(r.task.time_range)
        self.assertIn("period", [s.name for s in r.clarification.missing_slots])
        self.assertEqual(self.turn("全年").task.time_range.start, "2025-01-01")

    def test_clear_genre_when_replacing_scope(self):
        self.turn("2025Q3 Rock销售额，不包括Rock And Roll")
        r = self.turn("改看全部音频。")
        self.assertEqual(r.task.entities, [])
        self.assertEqual(r.task.excluded_entities, [])
        self.assertNotIn("Rock", r.task.normalized_question)

    def test_new_task_discards_comparison(self):
        self.turn("2025Q2全音频销售额")
        self.turn("换成第三季度")
        self.turn("新问题：报名截止是什么时候？")
        self.assertIsNone(self.m.previous_view)
