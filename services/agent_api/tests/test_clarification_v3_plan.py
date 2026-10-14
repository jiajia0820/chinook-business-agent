import unittest
from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class ClarificationPlanTests(unittest.TestCase):
    def setUp(self):
        self.p = ProfileRegistry.defaults().get("chinook-music")
        self.m = SessionMemory(profile_id=self.p.profile_id, config_version=self.p.config_version)

    def parse(self, text):
        return parse_question(text, self.p, self.m)

    def test_dimension_before_product_without_losing_product_blocker(self):
        for text in ("某商品属于什么类型？", "某商品属于什么类型"):
            result = self.parse(text)
            self.assertEqual([s.name for s in result.clarification.missing_slots], ["entity_dimension"])
            self.assertIn("entity", result.blocking_slots)
            self.assertIn("所属Album", result.clarification.options)

    def test_genre_before_period_without_assuming_a_year(self):
        result = self.parse("摇滚相关销售额是多少？")
        self.assertEqual([s.name for s in result.clarification.missing_slots], ["genre_scope"])
        self.assertIn("period", result.blocking_slots)
        self.assertNotIn("year", result.task.slots)

    def test_explicit_metric_not_repeated_for_target(self):
        # Naming both target measures fixes which target to verify, so the
        # paired-target choice must not be asked again.
        result = self.parse("第三季度全音频销售额和销量是否都达标？")
        self.assertNotIn("target_metric", result.blocking_slots)
        # Naming only the actual measure leaves the handbook's paired sales and
        # units target unresolved, so the target metric stays an open slot.
        ambiguous = self.parse("销售额达到目标了吗？")
        self.assertEqual(set(s.name for s in ambiguous.clarification.missing_slots),
                         {"period", "scope", "target_metric"})

    def test_unknown_metric_has_a_goal_choice(self):
        result = self.parse("第三季度Rock卖得怎么样？")
        self.assertIn("目标达成情况", result.clarification.options)
        self.assertIn("year", result.blocking_slots)
