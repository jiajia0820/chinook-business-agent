import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class LiveNl2SqlParsingTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.profile.sql_backend.model_mode = "live"
        self.memory = SessionMemory(
            profile_id=self.profile.profile_id,
            config_version=self.profile.config_version,
        )

    def test_live_customer_count_enters_sql_route(self):
        parsed = parse_question("客户数量是多少？", self.profile, self.memory)
        self.assertEqual((parsed.decision, parsed.task.route), ("ready", "sql"))
        self.assertEqual(parsed.task.business_metric_ids, ["customer_count"])
        self.assertEqual(parsed.task.normalized_question, "客户数量是多少？")

    def test_live_country_filter_is_preserved_for_model(self):
        question = "美国客户数量是多少？"
        parsed = parse_question(question, self.profile, self.memory)
        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.original_question, question)
        self.assertEqual(parsed.task.entities[0]["id"], "Country:USA")

    def test_live_quarterly_rock_sales_has_bound_dates_and_media(self):
        question = "2025年第三季度 Rock 音频销售额是多少？"
        parsed = parse_question(question, self.profile, self.memory)
        self.assertEqual(parsed.decision, "ready")
        self.assertEqual(parsed.task.business_metric_ids, ["sales_amount"])
        self.assertEqual(parsed.task.slots["start_date"], "2025-07-01")
        self.assertEqual(parsed.task.slots["end_date"], "2025-10-01")
        self.assertEqual(parsed.task.slots["media_ids"], [1, 2, 4, 5])
        self.assertEqual(parsed.task.original_question, question)


if __name__ == "__main__":
    unittest.main()
