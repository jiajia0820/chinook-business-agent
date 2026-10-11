import unittest

from services.agent_api.app.agent.state import BusinessTask, SessionMemory
from services.agent_api.app.agent.task_policy import semantic_task, typed_periods
from services.agent_api.app.profiles import ProfileRegistry


class CompatibleStateTests(unittest.TestCase):
    def test_old_task_and_memory_roundtrip(self):
        task = BusinessTask(original_question="客户数", normalized_question="客户数", intent="fact_query", route="sql")
        self.assertEqual(task.required_outputs, [])
        memory = SessionMemory(profile_id="test", config_version="v1", pending=task)
        self.assertEqual(SessionMemory.model_validate_json(memory.model_dump_json()), memory)
        self.assertEqual(memory.policy_version, "legacy")


class SemanticPolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def test_identifiers_are_not_years(self):
        self.assertNotIn("year", typed_periods("TrackId 1791-1799 第三季度")[0])
        self.assertEqual(typed_periods("2025年9月 TrackId 1791")[0]["year"], 2025)

    def test_composite_routes_and_plan(self):
        cases = [("第三季度销售额最高的品类是什么，运营手册建议关注什么？", "cross_source"),
                 ("每个播放列表收录了多少个不同商品？", "sql"),
                 ("2025年第三季度购买音频金额最高的五位客户是谁？", "sql"),
                 ("D03规定Metal和Heavy Metal分开时，D05选品用哪个Genre？", "rag"),
                 ("2025年第三季度全音频月度销售额计划合计是多少？", "rag")]
        for question, route in cases:
            with self.subTest(question=question):
                self.assertEqual(semantic_task(question, self.profile).route, route)

    def test_negation_is_separate_and_scope_synonyms(self):
        task = semantic_task("2025 Q3 Rock 不包括Rock And Roll", self.profile)
        self.assertEqual(len(task.entities), 1)
        self.assertEqual(task.excluded_entities[0]["label"], "Rock And Roll")
        self.assertEqual(semantic_task("全音频销售额", self.profile).slots["scope"], "全部音频")

    def test_primary_grain_precedes_display_attributes(self):
        task = semantic_task("2025年第三季度销售额最高的五个商品，同时列专辑和艺术家", self.profile)
        self.assertEqual(task.business_metric_ids, ["track_sales"])

    def test_document_caliber_and_target_are_not_actual_queries(self):
        for question in ("金属类默认按哪个分类统计？", "音频商品包含哪些媒体格式？", "目标表中Metal的销售额目标和销量目标是多少？"):
            with self.subTest(question=question):
                self.assertEqual(semantic_task(question, self.profile).route, "rag")
        self.assertEqual(semantic_task("2025Q3 Metal实际销售额是多少，目标是多少？", self.profile).route, "cross_source")
