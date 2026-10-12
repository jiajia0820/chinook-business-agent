import json
import unittest

from services.agent_api.app.contracts import DocumentChunk
from services.agent_api.app.response.document_answer import draft_answer, validate_model_answer


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

    def test_rock_alias_answer_preserves_rock_and_roll_boundary(self):
        docs = [chunk("D03", "“Rock”“摇滚”“Rock类”默认映射Genre:1。Rock And Roll是Genre:5。")]
        answer = draft_answer("“摇滚乐”在分类表里通常对应什么？", docs)
        self.assertIn("Genre:1", answer)
        self.assertIn("Genre:5", answer)
