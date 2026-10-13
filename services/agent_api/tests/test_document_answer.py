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
