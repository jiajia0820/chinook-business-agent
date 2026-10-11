"""Pending replies must improve a bounded task, never guess missing values."""
import unittest

from services.agent_api.app.agent.parsing import parse_question
from services.agent_api.app.agent.state import SessionMemory
from services.agent_api.app.profiles import ProfileRegistry


class ClarificationReplyTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")
        self.memory = SessionMemory(profile_id=self.profile.profile_id, config_version=self.profile.config_version)

    def turn(self, text):
        result = parse_question(text, self.profile, self.memory)
        self.memory.pending, self.memory.last_task = (result.task, None) if result.decision == "clarification" else (None, result.task)
        self.memory.pending_slots = [slot.name for slot in result.clarification.missing_slots] if result.clarification else []
        self.memory.pending_clarification = ({"task_id": result.task.task_id, "task_version": result.task.task_version,
            "slots": self.memory.pending_slots, "blocking_slots": result.blocking_slots,
            "options": result.clarification.options} if self.memory.pending else {})
        return result

    def names(self, result):
        return [slot.name for slot in result.clarification.missing_slots]

    def test_goal_choice_then_metric_then_year(self):
        initial = self.turn("第三季度Rock卖得怎么样？")
        goal = self.turn("目标达成情况")
        self.assertEqual(goal.task.task_id, initial.task.task_id)
        self.assertEqual(goal.task.intent, "target_attainment")
        self.assertEqual(goal.task.route, "cross_source")
        self.assertEqual(self.names(goal), ["metric"])
        self.assertEqual(goal.task.business_metric_ids, [])
        metric = self.turn("销售额和销量")
        self.assertEqual(self.names(metric), ["year"])
        done = self.turn("2025年")
        self.assertEqual(done.decision, "ready")
        self.assertEqual(set(done.task.business_metric_ids), {"sales_amount", "units_sold"})
        self.assertEqual(done.task.slots["quarter"], 3)
        self.assertEqual(done.task.intent, "target_attainment")

    def test_metric_choice_does_not_ask_confirmed_quarter(self):
        self.turn("第三季度Rock卖得怎么样？")
        result = self.turn("销量")
        self.assertEqual(self.names(result), ["year"])
        self.assertEqual(self.turn("2025年").decision, "ready")

    def test_dimension_choice_then_identity_exits_without_general_rule_answer(self):
        self.turn("某商品属于什么类型？")
        result = self.turn("Genre品类")
        self.assertEqual(self.names(result), ["entity"])
        self.assertEqual(result.task.slots["classification_dimension"], "Genre")
        result = self.turn("TrackId 1")
        self.assertEqual(result.decision, "unsupported")
        self.assertEqual(result.task.slots["classification_entity"], "TrackId:1")
        self.assertNotIn("year", result.task.slots)

    def test_identity_first_then_dimension(self):
        self.turn("某商品属于什么类型？")
        result = self.turn("TrackId 1791")
        self.assertEqual(self.names(result), ["entity_dimension"])
        result = self.turn("所属Album")
        self.assertEqual(result.decision, "unsupported")
        self.assertEqual(result.task.slots["classification_entity"], "TrackId:1791")
        self.assertEqual(result.task.slots["classification_dimension"], "Album")

    def test_literal_product_name_retains_pending_dimension_not_time(self):
        for name in ("今年2025", "Genre2025"):
            self.setUp()
            self.turn("某商品属于什么类型？")
            self.turn("MediaType媒体格式")
            result = self.turn("商品名称：" + name)
            self.assertEqual(result.decision, "unsupported")
            self.assertEqual(result.task.slots["classification_entity"], "TrackName:" + name)
            self.assertEqual(result.task.slots["classification_dimension"], "MediaType")
            self.assertNotIn("year", result.task.slots)
            self.assertEqual(result.task.time_issues, [])

    def test_profile_default_is_not_user_confirmation(self):
        result = self.turn("2025年第三季度Rock达到目标了吗？")
        self.assertEqual(self.names(result), ["metric"])
        self.assertFalse(result.task.slot_provenance["metric"]["confirmed"])
        result = self.turn("销量")
        self.assertEqual(result.decision, "ready")
        self.assertEqual(result.task.business_metric_ids, ["units_sold"])

    def test_range_not_a_partial_identity(self):
        result = self.turn("TrackId 1791至1800属于什么类型？")
        self.assertNotIn("classification_entity", result.task.slots)
        self.assertNotIn("year", result.task.slots)

    def test_track_and_genre_words_do_not_override_transaction_request(self):
        result = self.turn("2025Q3 TrackId 1791至1799按Genre品类查询实际销售额是多少？")
        self.assertNotEqual(result.task.intent, "classification_lookup")
        self.assertEqual(result.task.business_metric_ids, ["sales_amount"])
        self.assertEqual(result.decision, "ready")

    def test_media_aggregate_retains_metric_and_requires_period(self):
        self.turn("音乐和视频销售额要分开看还是合计？")
        result = self.turn("合并音视频")
        self.assertEqual(self.names(result), ["period"])
        self.assertEqual(result.task.business_metric_ids, ["sales_amount"])
        done = self.turn("2025Q3")
        self.assertEqual(done.decision, "ready")
        self.assertEqual(done.task.slots["media_ids"], [1, 2, 3, 4, 5])

    def test_separate_media_is_not_aggregate_or_endless_prompt(self):
        self.turn("音频与视频销售额分别统计还是合并？")
        result = self.turn("分别统计")
        self.assertEqual(result.decision, "unsupported")
        self.assertEqual(result.task.slots["media_aggregation"], "separate")
        self.assertIn("未用合计值替代", result.reason)

    def test_genre_union_year_only_then_quarter(self):
        self.turn("摇滚相关销售额是多少？")
        result = self.turn("Rock与Rock And Roll合并")
        self.assertEqual(self.names(result), ["period"])
        self.assertEqual({e["id"] for e in result.task.entities if e["type"] == "genre"}, {"Genre:1", "Genre:5"})
        result = self.turn("2025年")
        self.assertEqual(self.names(result), ["period"])
        self.assertIsNone(result.task.time_range)
        done = self.turn("第三季度")
        self.assertEqual(done.decision, "ready")
        self.assertEqual(done.task.time_range.start, "2025-07-01")

    def test_ordinary_conflicting_year_requires_explicit_correction(self):
        self.turn("第三季度Rock卖得怎么样？")
        self.turn("2025年")
        result = self.turn("2024年")
        self.assertEqual(self.names(result), ["condition_conflict"])
        self.assertEqual(result.task.slots["year"], 2025)
        result = self.turn("改成2024年")
        self.assertEqual(result.task.slots["year"], 2024)
        self.assertEqual(self.names(result), ["metric"])

    def test_ordinary_conflicting_metric_requires_explicit_correction(self):
        self.turn("第三季度Rock销售额是多少？")
        result = self.turn("销量")
        self.assertEqual(self.names(result), ["condition_conflict"])
        self.assertEqual(result.task.business_metric_ids, ["sales_amount"])
        result = self.turn("改查销量")
        self.assertEqual(result.task.business_metric_ids, ["units_sold"])
        self.assertEqual(self.names(result), ["year"])

    def test_expired_reply_does_not_change_bound_task(self):
        self.turn("第三季度Rock销售额是多少？")
        self.memory.pending_clarification["task_version"] += 1
        result = self.turn("2025年")
        self.assertEqual(self.names(result), ["context"])
        self.assertNotIn("year", result.task.slots)
        self.assertIn("已过期", result.clarification.question)

    def test_confirmation_is_not_a_choice(self):
        self.turn("销售额达到目标了吗？")
        result = self.turn("是")
        self.assertEqual(result.decision, "clarification")
        self.assertNotIn("year", result.task.slots)
        self.assertNotIn("scope", result.task.slots)
        self.assertIn("未自动填写", result.clarification.question)

    def test_invalid_quarter_never_falls_back_to_year(self):
        for text in ("2025Q5音频销售额是多少？", "2025年第五季度音频销售额是多少？", "2025Q12音频销售额是多少？"):
            self.setUp()
            result = self.turn(text)
            self.assertEqual(self.names(result), ["time_range"])
            self.assertEqual(self.turn("2025年").decision, "clarification")
            result = self.turn("改成2025Q3")
            self.assertEqual(result.decision, "ready")
            self.assertEqual(result.task.time_range.start, "2025-07-01")
            self.assertEqual(result.task.time_issues, [])
            self.assertNotIn("Q5", result.task.normalized_question)
            self.assertNotIn("Q12", result.task.normalized_question)

    def test_invalid_month_recovery(self):
        result = self.turn("2025年13月音频销售额是多少？")
        self.assertEqual(self.names(result), ["time_range"])
        result = self.turn("改成2025年8月")
        self.assertEqual(result.decision, "ready")
        self.assertEqual(result.task.time_range.start, "2025-08-01")
        self.assertNotIn("13月", result.task.normalized_question)

    def test_invalid_date_range_requires_valid_replacement(self):
        for text in ("2025-13-01至2025-13-02音频销售额是多少？", "2025-08-02至2025-07-01音频销售额是多少？"):
            self.setUp()
            self.assertEqual(self.names(self.turn(text)), ["time_range"])
            self.assertEqual(self.names(self.turn("2025年")), ["time_range"])
            self.assertEqual(self.turn("改成2025Q3").decision, "ready")

    def test_unknown_confirmation_preserves_task(self):
        first = self.turn("销售额达到目标了吗？")
        result = self.turn("不知道")
        self.assertEqual(result.task.task_id, first.task.task_id)
        self.assertIn("未自动填写", result.clarification.question)

    def test_relative_period_requires_explicit_complete_period(self):
        self.turn("今年音频销售额是多少？")
        self.assertEqual(self.names(self.turn("2025年")), ["time_range"])
        result = self.turn("2025年全年")
        self.assertEqual(result.decision, "ready")
        self.assertNotIn("今年", result.task.normalized_question)

    def test_new_question_does_not_apply_stale_reply(self):
        self.turn("第三季度Rock销售额是多少？")
        self.memory.pending_clarification["task_id"] = "old-task"
        result = self.turn("新问题：客户怎么分层？")
        self.assertEqual(result.decision, "ready")
        self.assertEqual(result.task.route, "rag")
        self.assertNotIn("quarter", result.task.slots)

    def test_high_value_list_after_period_is_not_only_rule_answer(self):
        self.turn("哪些是高价值客户？")
        result = self.turn("2025Q3")
        self.assertEqual(result.decision, "unsupported")
        self.assertIn("customer_list_capability_gap", result.rules)

    def test_prompt_instructions_are_not_clickable_values(self):
        result = self.turn("Rock销售额是多少？")
        self.assertFalse(any(option.startswith("填写") for option in result.clarification.options))


if __name__ == "__main__":
    unittest.main()
