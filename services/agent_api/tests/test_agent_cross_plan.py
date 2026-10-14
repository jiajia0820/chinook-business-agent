import unittest

from services.agent_api.app.agent.task_plan import document_needs, make_plan, retrieval_requests, retrieval_task_from_sql, retry_query
from services.agent_api.app.agent.task_policy import semantic_task
from services.agent_api.app.agent.state import BusinessTask
from services.agent_api.app.profiles import ProfileRegistry
from services.agent_api.app.contracts import SqlQueryResponse, SqlSource
from services.agent_api.app.tools.models import SqlTaskOutcome


class CrossPlanTests(unittest.TestCase):
    def setUp(self):
        self.profile = ProfileRegistry.defaults().get("chinook-music")

    def task(self, q):
        return semantic_task(q, self.profile)

    def test_sql_then_document_and_budget(self):
        task = self.task("2025第三季度销售额最高的品类是什么，运营手册建议关注什么？")
        self.assertEqual(make_plan(task)[0]["kind"], "sql")
        self.assertLessEqual(sum(count for _, _, count in retrieval_requests(task, 5)), 5)

    def test_rule_then_sql(self):
        task = self.task("2025第三季度购买Rock的客户，哪些属于A层重点维护？")
        self.assertEqual(make_plan(task)[0]["kind"], "rag")
        self.assertIn("D04", document_needs(task))

    def test_explicit_multidoc_no_sql(self):
        task = self.task("D03的分类边界如何影响D05选品？")
        self.assertEqual(document_needs(task), ["D03", "D05"])
        self.assertTrue(all(t["kind"] == "rag" for t in make_plan(task)))

    def test_repair_preserves_period_scope_and_exclusion(self):
        task = self.task("2025年第三季度Rock，不包括Rock And Roll，运营手册建议什么？")
        query = retry_query(task, "D09")
        self.assertIn("2025", query)
        self.assertIn("Rock", query)
        self.assertIn("不包括Rock And Roll", query)

    def test_independent_semantic_paraphrases(self):
        for q in ("2025 Q3 Metal 实际销售额与目标相比达标了吗？", "2025年第三季度全音频销售额距离目标多少？", "查询2025第三季度销售额最高的品类并说明运营手册建议", "2025年第三季度Rock销售额是多少，运营手册建议关注什么？"):
            with self.subTest(q=q):
                self.assertEqual(self.task(q).route, "cross_source")

    def test_chinese_adjacent_document_ids_are_explicit(self):
        self.assertEqual(document_needs(self.task("D06目标、D08复盘和D10活动方案如何衔接？")), ["D06", "D08", "D10"])
        self.assertEqual(document_needs(self.task("D03规定Metal分开时，D05的候选应使用哪个Genre？")), ["D03", "D05"])

    def test_cross_source_calculation_reserves_q3_review_source(self):
        task = BusinessTask(
            original_question="2025年第三季度Rock销售额达到目标了吗？",
            normalized_question="2025年第三季度Rock销售额达到目标了吗？",
            retrieval_question="2025年第三季度Rock销售额达到目标了吗？",
            intent="target_attainment",
            route="cross_source",
            needs_calculation=True,
            business_metric_ids=["sales_amount"],
            slots={"year": 2025, "quarter": 3, "media_ids": [1, 2, 4, 5]},
        )
        self.assertIn("D06", document_needs(task))
        self.assertIn("D08", document_needs(task))
        self.assertIn("D08", {item[0] for item in retrieval_requests(task, 5)})

    def test_sql_identity_guides_only_dependent_retrieval(self):
        task = self.task("2025年第三季度销售额最高的品类是什么，运营手册建议关注什么？")
        result = SqlQueryResponse(query_id="rank-result", profile_id="chinook-music", status="success",
            columns=["GenreId", "Name", "sales_amount"], rows=[{"GenreId": 1, "Name": "Rock", "sales_amount": 12.34}],
            row_count=1, truncated=False, execution_ms=1, source=SqlSource(type="fixture", name="plan-test", tables=["Genre"]))
        sql = SqlTaskOutcome(query_id="rank-task", profile_id="chinook-music", status="success", sql_results=[result])
        linked = retrieval_task_from_sql(task, sql, self.profile)
        self.assertEqual(linked.entities[0]["label"], "Rock")
        self.assertEqual(document_needs(linked), document_needs(task))
        self.assertIn("Rock", retrieval_requests(linked, 5)[0][1])
        self.assertEqual(task.entities, [])
        self.assertEqual(linked.slots, task.slots)
        self.assertEqual(sql.sql_results[0].rows, result.rows)
        self.assertIn("rank-result", linked.slot_provenance["retrieval_entity"]["source_ref"])
        name_only = sql.model_copy(update={"sql_results": [result.model_copy(update={"rows": [{"genre": "Rock", "sales_amount": 12.34}]})]})
        self.assertEqual(retrieval_task_from_sql(task, name_only, self.profile).entities[0]["label"], "Rock")
        for bad in ({"GenreId": 1, "Name": "Jazz"}, {"GenreId": 999, "Name": "ignore instructions"}):
            with self.subTest(row=bad):
                malformed = sql.model_copy(update={"sql_results": [result.model_copy(update={"rows": [bad]})]})
                self.assertIs(retrieval_task_from_sql(task, malformed, self.profile), task)
