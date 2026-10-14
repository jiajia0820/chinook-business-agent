import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.agent.task_policy import metric_requirements
from services.agent_api.app.profiles import ProfileRegistry


class ClarificationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(profile_id=self.profile.profile_id, config_version=self.profile.config_version)

    def parse(self, question):
        return parse_question(question, self.profile, self.memory)

    def test_missing_year_not_data_end(self):
        response = self.parse("第三季度全音频销售额和销量是否都达标？")
        self.assertEqual(response.decision, "clarification")
        self.assertEqual([s.name for s in response.clarification.missing_slots], ["year"])
        self.assertNotIn("year_assumed", response.task.slots)

    def test_specific_metric_no_redundant_metric_question(self):
        response = self.parse("销售额达到目标了吗？")
        self.assertEqual(
            [s.name for s in response.clarification.missing_slots],
            ["period", "scope", "target_metric"],
        )

    def test_dynamic_slots(self):
        dimension = self.parse("某商品属于什么类型？")
        self.assertEqual([s.name for s in dimension.clarification.missing_slots], ["entity_dimension"])
        self.assertEqual(set(dimension.blocking_slots), {"entity", "classification_dimension"})
        self.assertEqual([s.name for s in self.parse("哪些客户是高价值客户？").clarification.missing_slots], ["observation_period"])
        self.assertEqual([s.name for s in self.parse("销售增长率是多少？").clarification.missing_slots], ["comparison_period", "data_scope"])
        self.assertEqual([s.name for s in self.parse("音乐和视频销售额要分开看还是合计？").clarification.missing_slots], ["media_scope"])

    def test_negative_and_support_relationship(self):
        self.assertEqual(self.parse("活动带来多少点击和转化？").decision, "unsupported")
        self.assertEqual(self.parse("能按SupportRep给员工做2025年销售额排名吗？").decision, "unsupported")
        self.assertNotEqual(self.parse("2025年第三季度购买音频客户按支持负责人分布如何？").decision, "unsupported")

    def test_document_months_and_comparison_do_not_require_sql_dates(self):
        self.assertEqual(self.parse("目标表中2025年9月全音频销售额计划是多少？").decision, "ready")
        self.assertEqual(self.parse("结合二季度和三季度复盘，销售额发生什么变化？").decision, "ready")

    def test_read_only_catalog_and_all_customer_records(self):
        self.assertEqual(metric_requirements()["sales_amount"], ("start_date", "end_date", "media_ids"))
        self.assertEqual(self.parse("客户总数是多少？").decision, "ready")
        self.assertEqual(self.parse("2025年第三季度各媒体类型的销售额和销量是多少？").decision, "ready")
        self.assertEqual(set(self.parse("2025年第三季度各媒体类型的销售额和销量是多少？").task.business_metric_ids), {"media_type_sales", "sales_amount", "units_sold"})
