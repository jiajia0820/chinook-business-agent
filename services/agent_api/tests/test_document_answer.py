import json
import unittest

from services.agent_api.app.contracts import DocumentChunk
from services.agent_api.app.response.document_answer import draft_answer, validate_model_answer

# The real D03 catalog shape: one header line, then "id | name | aliases | relation".
CATALOG = """GenreId | 标准名称 | 常见中文说法 | 默认关系
1 | Rock | 摇滚 | 与Rock And Roll分开
3 | Metal | 金属 | 与Heavy Metal分开
5 | Rock And Roll | 摇滚乐、摇滚与摇滚乐 | 不并入Rock
7 | Latin | 拉丁 | 独立分类
11 | Bossa Nova | 巴萨诺瓦 | 独立分类
13 | Heavy Metal | 重金属 | 不并入Metal
15 | Electronica/Dance | 电子、舞曲 | 保留斜线
23 | Alternative | 另类 | 不自动并入Alternative & Punk
24 | Classical | 古典 | 独立分类"""


def chunk(doc_id, text, suffix="1"):
    return DocumentChunk(doc_id=doc_id, chunk_id=f"{doc_id}-{suffix}", title=doc_id,
                         doc_type="pdf", text=text, source_uri=f"knowledge/{doc_id}#P1")


class DocumentAnswerTests(unittest.TestCase):
    def test_comparison_uses_current_evidence_values_rather_than_fixed_answers(self):
        docs = [chunk("D07", "2025年第二季度经营复盘。二季度音频销售额100.00美元，销量110件，19张订单，18位购买客户。"),
                chunk("D08", "2025年第三季度经营复盘。第三季度音频销售额105.00美元，销量114件，21张订单，20位购买客户。")]
        answer = draft_answer("结合二季度和三季度复盘，销售额、订单数和购买客户数发生了什么变化？", docs)
        self.assertIn("100.00", answer)
        self.assertIn("105.00", answer)
        self.assertIn("5.00", answer)
        self.assertIn("[D07]", answer)
        self.assertNotIn("108.90", answer)

    def test_incomplete_comparison_does_not_invent_the_other_period(self):
        docs = [chunk("D07", "二季度音频销售额100.00美元，销量110件，19张订单，18位购买客户。")]
        self.assertIsNone(draft_answer("结合二季度和三季度复盘，销售额发生了什么变化？", docs))

    def test_model_cannot_cite_an_unretrieved_document(self):
        docs = [chunk("D03", "Metal为Genre:3，不自动合并Heavy Metal。")]
        payload = json.dumps({"claims": [{"text": "Metal为Genre:3。", "evidence": [{"chunk_id": "D99-1", "quote": docs[0].text}]}]})
        with self.assertRaises(ValueError):
            validate_model_answer(payload, docs)

    def test_model_cannot_invent_numbers_or_quotes(self):
        docs = [chunk("D03", "Metal为Genre:3，不自动合并Heavy Metal。")]
        for text, quote in [("Metal为Genre:99。", docs[0].text), ("Metal为Genre:3。", "Metal和重金属是同一种类型")]:
            payload = json.dumps({"claims": [{"text": text, "evidence": [{"chunk_id": "D03-1", "quote": quote}]}]})
            with self.subTest(text=text, quote=quote), self.assertRaises(ValueError):
                validate_model_answer(payload, docs)

    def test_model_answer_is_short_and_keeps_only_readable_references(self):
        docs = [chunk("D03", "Metal为Genre:3，不自动合并Heavy Metal。")]
        payload = json.dumps({"claims": [{"text": "Metal为Genre:3，不自动合并Heavy Metal。", "evidence": [{"chunk_id": "D03-1", "quote": docs[0].text}]}]})
        self.assertEqual(validate_model_answer(payload, docs), "Metal为Genre:3，不自动合并Heavy Metal。\n依据：[D03]。")

    def test_numeric_format_and_table_pipes_do_not_break_grounding(self):
        docs = [chunk("D07", "| 指标 | 金额 |\n| 音频 | 50.00 |")]
        payload = json.dumps({"claims": [{"text": "音频金额为50美元。",
                                          "evidence": [{"chunk_id": "D07-1", "quote": "| 音频 | 50.00 |"}]}]})
        self.assertEqual(validate_model_answer(payload, docs), "音频金额为50美元。\n依据：[D07]。")

    def test_invented_number_still_rejected_after_relaxation(self):
        docs = [chunk("D07", "| 指标 | 金额 |\n| 音频 | 50.00 |")]
        payload = json.dumps({"claims": [{"text": "音频金额为60美元。",
                                          "evidence": [{"chunk_id": "D07-1", "quote": "| 音频 | 50.00 |"}]}]})
        with self.assertRaises(ValueError):
            validate_model_answer(payload, docs)

    def test_answer_may_use_more_than_six_claims(self):
        docs = [chunk("D03", "Metal为Genre:3，不自动合并Heavy Metal。")]
        claims = [{"text": "Metal为Genre:3。", "evidence": [{"chunk_id": "D03-1", "quote": docs[0].text}]}] * 7
        answer = validate_model_answer(json.dumps({"claims": claims}), docs)
        self.assertEqual(answer.count("Metal为Genre:3。"), 7)

    def test_table_quote_with_escaped_newlines_is_still_grounded(self):
        docs = [chunk("D06", "统计范围\n销售额目标 美元\nRock\n50.00\n主要增长品类")]
        payload = json.dumps({"claims": [{
            "text": "Rock 销售额目标为50.00美元，定位主要增长品类。",
            "evidence": [{"chunk_id": "D06-1", "quote": "Rock\\n50.00\\n主要增长品类"}]}]})
        self.assertEqual(validate_model_answer(payload, docs),
                         "Rock 销售额目标为50.00美元，定位主要增长品类。\n依据：[D06]。")

    def test_extra_keys_and_one_bad_claim_do_not_void_the_answer(self):
        docs = [chunk("D06", "Rock 销售额目标为50.00美元。"), chunk("D08", "第三季度音频销售额105.00美元。")]
        claims = [
            {"text": "Rock 目标为50.00美元。",
             "evidence": [{"chunk_id": "D06-1", "doc_id": "D06", "quote": "Rock 销售额目标为50.00美元。"}]},
            {"text": "第三季度音频销售额为105.00美元。",
             "evidence": [{"chunk_id": "D06-1", "doc_id": "D06", "quote": "第三季度音频销售额105.00美元。"}]},
            {"text": "无任何依据的结论。", "evidence": []},
        ]
        answer = validate_model_answer(json.dumps({"claims": claims}), docs)
        self.assertIn("Rock 目标为50.00美元。", answer)
        self.assertIn("第三季度音频销售额为105.00美元。", answer)
        self.assertNotIn("无任何依据的结论。", answer)
        self.assertIn("[D06]", answer)
        self.assertIn("[D08]", answer)

    def test_rock_alias_answer_preserves_rock_and_roll_boundary(self):
        docs = [chunk("D03", "“Rock”“摇滚”“Rock类”默认映射Genre:1。Rock And Roll是Genre:5。")]
        answer = draft_answer("“摇滚乐”在分类表里通常对应什么？", docs)
        self.assertIn("Genre:1", answer)
        self.assertIn("Genre:5", answer)


class GenreCatalogTests(unittest.TestCase):
    """Aliases outside the two hardcoded prose sentences still need their Genre id."""

    def test_every_named_alias_maps_to_its_own_catalog_id(self):
        cases = [
            ("Alternative这个分类在日报里通常对应哪个Genre？", "Genre:23"),
            ("古典音乐商品通常按哪个Genre统计？", "Genre:24"),
            ("电子/舞曲类商品在分类表里对应哪个Genre？", "Genre:15"),
            ("日报里写“拉丁类”，和Latin的统计口径是同一个吗？", "Genre:7"),
        ]
        docs = [chunk("D03", CATALOG)]
        for question, want in cases:
            with self.subTest(question=question):
                answer = draft_answer(question, docs)
                self.assertIn(want, answer)
                self.assertNotIn("Bossa Nova", answer)

    def test_catalog_answer_keeps_the_sibling_boundary(self):
        answer = draft_answer("季度目标里的Rock要不要一起算进去？", [chunk("D03", CATALOG)])
        self.assertIn("Genre:1", answer)
        self.assertIn("Genre:5", answer)

    def test_fusion_append_keeps_only_statements_naming_the_entity(self):
        docs = [chunk("D03", CATALOG),
                chunk("D05", "选品清单保持数据库标准分类，中文简称和专题主题写在展示层，不在目录中改写名称。\n"
                             "Rock未达目标时，商品清单围绕Genre:1成交商品和专辑延伸做选品。")]
        answer = draft_answer("D03的分类边界如何影响D05的Rock选品？", docs)
        self.assertIn("Genre:1", answer)
        self.assertIn("Rock未达目标时", answer)
        self.assertNotIn("展示层", answer)
        self.assertIn("[D05]", answer)


# D04 层级定义表与 D02 指标口径表的真实逐单元格形态。
TIER_TABLE = """层级 | 名称 | 当季音频销售额 美元 | 辅助观察 | 第三季度人数
A | 重点维护 | 大于等于 12.00 | 订单数、件数、品类集中度 | 3
B | 常规维护 | 5.00 至 12.00 以下 | 订单数、复购间隔 | 6
C | 基础服务 | 0 以上至 5.00 以下 | 是否首次购买 | 10"""

METRIC_TABLE = """指标 | 计算口径 | 单位 | 应同时说明的条件
销售额 | SUM(InvoiceLine.UnitPrice * InvoiceLine.Quantity) | 美元 | 订单日期、媒体类型和品类范围
订单数 | COUNT(DISTINCT Invoice.InvoiceId) | 张 | 筛选范围内去重
客单价 | 同范围销售额 ÷ 同范围订单数 | 美元每单 | 无订单时记为不可计算
成交均价 | 同范围销售额 ÷ 同范围销量 | 美元每件 | 与客单价区分，不平均商品标价"""


class FocusedRendererTests(unittest.TestCase):
    """三类换词问题的聚焦渲染器：谓词只看问题，取值只看证据。"""

    def test_tier_predicate_needs_a_role_word_and_an_ask_word(self):
        from services.agent_api.app.response.document_answer import asks_tier_thresholds
        for question in ("给顾客分三档时，各档的消费金额界线分别是多少？",
                         "客户分层的A、B、C层金额阈值分别是什么？",
                         "客户分三档的金额标准分别是什么？"):
            with self.subTest(question=question):
                self.assertTrue(asks_tier_thresholds(question))
        for question in ("平均一笔交易卖多少钱，除以件数还是成交笔数？",
                         "客户分层流程由哪个岗位负责？",
                         "销售额指标定义是什么？"):
            with self.subTest(question=question):
                self.assertFalse(asks_tier_thresholds(question))

    def test_customer_tiers_keeps_only_the_boundary_cells(self):
        from services.agent_api.app.response.document_answer import customer_tiers
        answer = customer_tiers("客户分层的A、B、C层金额阈值分别是什么？", [chunk("D04", TIER_TABLE)])
        self.assertIn("大于等于 12.00", answer)
        self.assertIn("5.00 至 12.00 以下", answer)
        self.assertIn("0 以上至 5.00 以下", answer)
        self.assertIn("当季音频销售额", answer)
        self.assertIn("[D04]", answer)
        self.assertNotIn("辅助观察", answer)
        self.assertNotIn("第三季度人数", answer)

    def test_customer_tiers_needs_a_boundary_table_not_a_result_table(self):
        from services.agent_api.app.response.document_answer import customer_tiers
        result_table = ("层级 | 客户编号 | 当季音频销售额 美元 | 支持负责人\n"
                        "A 重点维护 | 10、31、48 | 15.84、13.86、15.84 | Margaret Park")
        self.assertIsNone(customer_tiers("客户分层的A、B、C层金额阈值分别是什么？", [chunk("D04", result_table)]))
        self.assertIsNone(customer_tiers("销售额指标定义是什么？", [chunk("D04", TIER_TABLE)]))

    def test_metric_denominator_predicate_needs_both_sides(self):
        from services.agent_api.app.response.document_answer import asks_metric_denominator
        self.assertTrue(asks_metric_denominator("平均一笔交易卖多少钱，除以件数还是成交笔数？没有成交怎么办？"))
        self.assertTrue(asks_metric_denominator("客单价是按销售额除以销量，还是除以订单数？"))
        self.assertFalse(asks_metric_denominator("销售额除以订单数得到什么指标？"))
        self.assertFalse(asks_metric_denominator("给顾客分三档时，各档的消费金额界线分别是多少？"))

    def test_metric_definition_separates_orders_from_units(self):
        from services.agent_api.app.response.document_answer import metric_definition
        answer = metric_definition("客单价是按销售额除以销量，还是除以订单数？订单数为零时怎么处理？",
                                   [chunk("D02", METRIC_TABLE)])
        self.assertIn("同范围销售额 ÷ 同范围订单数", answer)
        self.assertIn("美元每单", answer)
        self.assertIn("成交均价", answer)
        self.assertIn("零订单", answer)
        self.assertIn("[D02]", answer)
        self.assertNotIn("SUM(InvoiceLine.UnitPrice", answer)

    def test_metric_definition_falls_back_without_the_metric_table(self):
        from services.agent_api.app.response.document_answer import metric_definition
        self.assertIsNone(metric_definition("客单价是按销售额除以销量，还是除以订单数？",
                                            [chunk("D02", "客单价用于观察单张订单金额。")]))

    def test_activity_plan_predicate_covers_paraphrased_wording(self):
        from services.agent_api.app.response.document_answer import asks_activity_plan
        self.assertTrue(asks_activity_plan("参加音乐专题征集最迟哪天交，得准备哪些东西？"))
        self.assertTrue(asks_activity_plan("海报上的报名截止是什么时候？"))
        self.assertFalse(asks_activity_plan("给顾客分三档时，各档的消费金额界线分别是多少？"))

    def test_poster_reads_deadline_and_materials_from_ocr_fragments(self):
        from services.agent_api.app.response.document_answer import poster
        docs = [chunk("D11", "报名截止\n10月13日18:00", "b30"),
                chunk("D11", "报名很简单\n商品清单+选品理由+一段页面文案\n提交至商品内容组/品类运营组", "b29")]
        answer = poster("参加音乐专题征集最迟哪天交，得准备哪些东西？", docs)
        self.assertEqual(answer, "报名截止：10月13日18:00。\n提交材料：商品清单、选品理由和一段页面文案。\n依据：[D11]。")
