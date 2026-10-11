import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.agent.task_plan import document_needs


class SessionUpdateTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(profile_id=self.profile.profile_id, config_version=self.profile.config_version)

    def parse(self, q):
        return parse_question(q, self.profile, self.memory)

    def test_pending_negation_and_year(self):
        first = self.parse("摇滚相关销售额是多少？")
        self.memory.pending = first.task
        turn = self.parse("按2025年第三季度Rock，不包括Rock And Roll。")
        self.assertEqual(turn.decision, "ready")
        self.assertTrue(turn.reused_context)
        self.assertEqual(len(turn.task.entities), 1)
        self.assertEqual(len(turn.task.excluded_entities), 1)

    def test_pending_topic_survives_period_reply(self):
        self.memory.pending = self.parse("客户怎么分层？").task
        turn = self.parse("按2025年第三季度。")
        self.assertEqual(turn.decision, "ready")
        self.assertIn("分层", turn.task.retrieval_question)
        self.assertIn("D04", document_needs(turn.task))

    def test_followup_and_correction(self):
        self.memory.last_task = self.parse("2025年第三季度Rock销售额是多少？").task
        follow = self.parse("和目标比呢？")
        self.assertEqual(follow.decision, "ready")
        self.assertEqual(follow.task.route, "cross_source")
        correction = self.parse("换成第二季度呢？")
        self.assertEqual(correction.task.slots["quarter"], 2)
        self.assertNotIn("第三季度", correction.task.normalized_question)

    def test_new_task_does_not_inherit(self):
        self.memory.last_task = self.parse("2025年第三季度Rock销售额是多少？").task
        new = self.parse("活动什么时候报名截止？")
        self.assertFalse(new.reused_context)
        self.assertEqual(new.task.entities, [])
        self.assertNotIn("year", new.task.slots)

    def test_activity_period_is_known_document_request(self):
        result = self.parse("活动什么时候举行？")
        self.assertEqual(result.decision, "ready")
        self.assertEqual(result.task.route, "rag")
        self.assertEqual(set(document_needs(result.task)), {"D10", "D11"})
