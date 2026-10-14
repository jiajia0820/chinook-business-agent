import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class BoundaryContractTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def parse(self, question):
        memory = SessionMemory(
            profile_id=self.profile.profile_id,
            config_version=self.profile.config_version,
        )
        return parse_question(question, self.profile, memory)

    def test_definition_questions_do_not_require_period(self):
        for question in (
            "全店销售额和音乐销售额有什么范围差异？",
            "经营分析写客单价时，指标说明和销售分析方法分别要求用什么分母？这两个指标有什么区别？",
        ):
            with self.subTest(question=question):
                result = self.parse(question)
                self.assertEqual(result.decision, "ready")
                self.assertEqual(result.task.route, "rag")

    def test_bare_target_question_requires_three_slots(self):
        result = self.parse("销售额达到目标了吗？")
        self.assertEqual(result.decision, "clarification")
        self.assertEqual(
            [slot.name for slot in result.clarification.missing_slots],
            ["period", "scope", "target_metric"],
        )

    def test_unsupported_answers_explain_the_data_gap(self):
        cases = (
            ("第三季度利润是多少？", ("成本", "利润")),
            ("活动页面带来了多少点击和转化？", ("事件", "日志", "转化")),
            ("能按SupportRep给员工做2025年销售额排名吗？", ("SupportRep", "维护", "销售")),
        )
        for question, tokens in cases:
            with self.subTest(question=question):
                result = self.parse(question)
                text = result.reason or ""
                self.assertEqual(result.decision, "unsupported")
                for token in tokens:
                    self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
