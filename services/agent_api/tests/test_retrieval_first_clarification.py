"""规则/方法类改写问题应先走文档检索，而不是被交易周期澄清挡住。

这些测试直接构造默认 profile 和空 SessionMemory，不启动服务、不读评测
标准答案，只验证 parse_question 的决策、路由和阻塞槽位。
"""

import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.knowledge.source_plan import rag_source_plan
from services.agent_api.app.profiles import ProfileRegistry


def parse_default(question: str):
    """用默认 chinook-music profile 和空会话解析一轮问题。"""
    profile = ProfileRegistry.defaults().get("chinook-music")
    memory = SessionMemory(profile_id=profile.profile_id, config_version=profile.config_version)
    return parse_question(question, profile, memory)


class RetrievalFirstClarificationTests(unittest.TestCase):
    def test_customer_threshold_paraphrase_routes_to_rag(self):
        turn = parse_default("给顾客分三档时，各档的消费金额界线分别是多少？")
        self.assertEqual(turn.decision, "ready")
        self.assertIsNotNone(turn.task)
        self.assertEqual(turn.task.route, "rag")
        self.assertNotIn("year", turn.blocking_slots)
        self.assertNotIn("period", turn.blocking_slots)

    def test_poster_deadline_paraphrase_routes_to_rag(self):
        turn = parse_default("参加音乐专题征集最迟哪天交，得准备哪些东西？")
        self.assertEqual(turn.decision, "ready")
        self.assertIsNotNone(turn.task)
        self.assertEqual(turn.task.route, "rag")

    def test_aov_definition_routes_to_rag(self):
        turn = parse_default("平均一笔交易卖多少钱，除以件数还是成交笔数？没有成交怎么办？")
        self.assertEqual(turn.decision, "ready")
        self.assertIsNotNone(turn.task)
        self.assertEqual(turn.task.route, "rag")

    def test_actual_sales_without_year_still_clarifies(self):
        turn = parse_default("第三季度音频销售额是多少？")
        self.assertEqual(turn.decision, "clarification")
        self.assertIn("year", turn.blocking_slots)


class SourcePlanParaphraseTests(unittest.TestCase):
    """换词后的规则问题要落到权威文档，而不是空来源计划。"""

    def test_customer_threshold_paraphrase_plans_d04(self):
        plan = rag_source_plan("给顾客分三档时，各档的消费金额界线分别是多少？")
        self.assertIn("D04", plan.primary_docs)

    def test_activity_deadline_paraphrase_plans_d11(self):
        plan = rag_source_plan("参加音乐专题征集最迟哪天交，得准备哪些东西？")
        self.assertIn("D11", plan.primary_docs)

    def test_aov_definition_paraphrase_plans_d02(self):
        plan = rag_source_plan("平均一笔交易卖多少钱，除以件数还是成交笔数？没有成交怎么办？")
        self.assertIn("D02", plan.primary_docs)

    def test_poster_selection_threshold_plans_d05(self):
        plan = rag_source_plan("上海报的商品是怎么挑出来的，有什么门槛？")
        self.assertIn("D05", plan.primary_docs)


if __name__ == "__main__":
    unittest.main()
