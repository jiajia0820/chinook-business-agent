import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class MultiTurnContractTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(
            profile_id=self.profile.profile_id,
            config_version=self.profile.config_version,
        )

    def test_customer_tiering_first_turn_requires_observation_period(self):
        result = parse_question("客户怎么分层？", self.profile, self.memory)
        self.assertEqual(result.decision, "clarification")
        self.assertEqual(
            [slot.name for slot in result.clarification.missing_slots],
            ["observation_period"],
        )

    def test_followup_preserves_confirmed_scope_for_operation_advice(self):
        first = parse_question("2025年第三季度Rock销售额是多少？", self.profile, self.memory)
        self.memory.last_task = first.task
        second = parse_question("和目标比呢？", self.profile, self.memory)
        self.memory.last_task = second.task
        third = parse_question("那下一步重点看什么？", self.profile, self.memory)
        self.assertEqual(third.decision, "ready")
        self.assertEqual(third.task.route, "rag")
        self.assertEqual(third.task.slots.get("scope"), "Rock")
        self.assertIn("Rock", third.task.retrieval_question)


if __name__ == "__main__":
    unittest.main()
