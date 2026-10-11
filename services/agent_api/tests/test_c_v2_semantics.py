import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class TaskSemanticsV2Tests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(profile_id=self.profile.profile_id, config_version=self.profile.config_version)

    def parse(self, text):
        return parse_question(text, self.profile, self.memory)

    def test_transaction_without_metric_cannot_bypass_year(self):
        r = self.parse("海报要求的Rock主页面商品中，TrackId 1791-1799在第三季度是否有成交？")
        self.assertEqual(r.decision, "clarification")
        self.assertEqual([x.name for x in r.clarification.missing_slots], ["year"])
        self.assertEqual(r.task.business_metric_ids, ["track_sales"])

    def test_unknown_goal_before_dependent_year(self):
        r = self.parse("第三季度Rock卖得怎么样？")
        self.assertEqual([x.name for x in r.clarification.missing_slots], ["metric"])

    def test_missing_observation_period_is_not_only_year(self):
        r = self.parse("摇滚相关销售额是多少？")
        self.assertIn("period", r.blocking_slots)
        self.assertEqual([x.name for x in r.clarification.missing_slots], ["genre_scope"])

    def test_four_classification_dimensions(self):
        options = self.parse("某商品属于什么类型？").clarification.options
        self.assertIn("所属Album", options)
        self.assertIn("所在Playlist", options)

    def test_report_anchor_is_not_database_query(self):
        r = self.parse("D08复盘中第三季度销售额是多少？")
        self.assertEqual(r.decision, "ready")
        self.assertEqual(r.task.route, "rag")
        self.assertIn("reported_actual", r.task.source_roles)

    def test_user_premise_is_not_verified(self):
        r = self.parse("第三季度Metal未达目标，活动方案把Metal放在什么位置？")
        self.assertEqual(r.task.route, "rag")
        self.assertTrue(r.task.assumptions)

    def test_explicit_annual_query_valid(self):
        r = self.parse("2025年音频销售额是多少？")
        self.assertEqual(r.decision, "ready")
        self.assertEqual(r.task.time_range.start, "2025-01-01")

    def test_general_customer_rules_do_not_require_a_cohort_period(self):
        for question in ("客户怎么分层？", "客户如何分层？", "客户分层规则是什么？"):
            with self.subTest(question=question):
                result = self.parse(question)
                self.assertEqual(result.decision, "ready")
                self.assertEqual(result.task.route, "rag")
                self.assertIn("customer_thresholds", result.task.answer_obligations)
        selected = self.parse("哪些客户是高价值客户？")
        self.assertEqual(selected.decision, "clarification")
        self.assertEqual([s.name for s in selected.clarification.missing_slots], ["observation_period"])
