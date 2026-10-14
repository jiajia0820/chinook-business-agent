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

    def test_tiering_period_reply_keeps_document_route(self):
        self.memory.pending = parse_question("客户怎么分层？", self.profile, self.memory).task
        reply = parse_question("按2025年第三季度。", self.profile, self.memory)
        self.assertEqual(reply.decision, "ready")
        self.assertEqual(reply.task.route, "rag")
        self.assertIn("分层", reply.task.retrieval_question)


if __name__ == "__main__":
    unittest.main()
