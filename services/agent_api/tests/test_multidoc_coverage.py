import unittest

from services.agent_api.app.contracts import DocumentChunk
from services.agent_api.app.response.document_answer import draft_answer
from services.agent_api.app.response.fast_renderer import local_answer
from services.agent_api.app.knowledge import facts


def chunk(doc_id: str, text: str) -> DocumentChunk:
    return DocumentChunk(
        doc_id=doc_id,
        chunk_id=f"{doc_id}-coverage",
        title=doc_id,
        doc_type="pdf",
        text=text,
        source_uri=f"knowledge/{doc_id}",
    )


class MultiDocumentCoverageTests(unittest.TestCase):
    def test_document_role_contracts_are_explicit_for_fusion_questions(self):
        roles = getattr(facts, "DOCUMENT_ROLES", {})
        self.assertEqual(
            roles.get("target_review_activity"),
            {"D06": "target", "D08": "actual_review", "D10": "operation"},
        )
        self.assertEqual(
            roles.get("customer_category_action"),
            {"D04": "segmentation", "D09": "category_operation"},
        )
        self.assertEqual(
            roles.get("activity_material_crosscheck"),
            {"D10": "schedule_and_version", "D11": "registration_material"},
        )
        self.assertIn("target_review_activity", facts.answer_obligations("D06目标、D08复盘和D10活动方案之间如何衔接？"))
        self.assertIn("customer_category_action", facts.answer_obligations("第三季度客户分层结果与Rock运营动作如何结合？"))
        self.assertIn("activity_material_crosscheck", facts.answer_obligations("活动上线前，活动方案和海报要共同核对哪些时间、材料和版本信息？"))

    def test_target_review_activity_names_each_document_role(self):
        answer = draft_answer(
            "D06目标、D08复盘和D10活动方案之间如何衔接？",
            [
                chunk("D06", "D06第三季度目标：Rock销售额50.00美元，销量50件。"),
                chunk("D08", "D08第三季度复盘：Rock实际销售额47.52美元，低于目标。"),
                chunk("D10", "D10依据差距安排第四季度Rock主页面和Metal延伸栏目。"),
            ],
        )
        self.assertIsNotNone(answer)
        self.assertIn("D06", answer)
        self.assertIn("D08", answer)
        self.assertIn("D10", answer)
        self.assertIn("目标", answer)
        self.assertIn("实际", answer)
        self.assertIn("Rock主页面", answer)
        self.assertIn("Metal延伸", answer)

    def test_customer_tiering_and_rock_actions_cover_all_required_points(self):
        answer = draft_answer(
            "第三季度客户分层结果与Rock运营动作如何结合？",
            [
                chunk("D04", "D04按第三季度金额阈值确定A、B、C层客户。"),
                chunk("D09", "D09的Rock动作重点是成交商品、专辑覆盖和专题陈列。"),
            ],
        )
        self.assertIsNotNone(answer)
        self.assertIn("[D04]", answer)
        self.assertIn("[D09]", answer)
        for fact in ("A", "B", "C", "成交商品", "专辑覆盖", "专题陈列"):
            self.assertIn(fact, answer)

    def test_activity_crosscheck_covers_schedule_deadline_materials_and_version(self):
        answer = draft_answer(
            "活动上线前，活动方案和海报要共同核对哪些时间、材料和版本信息？",
            [
                chunk("D10", "D10拟执行周期：2025年10月20日至2025年11月2日。上线前核对商品清单和页面版本。"),
                chunk("D11", "D11商品报名截止：2025年10月13日18:00。提交商品清单、选品理由和页面文案。"),
            ],
        )
        self.assertIsNotNone(answer)
        self.assertIn("2025年10月20日至11月2日", answer)
        self.assertIn("10月13日18:00", answer)
        for fact in ("商品清单", "选品理由", "页面文案", "版本"):
            self.assertIn(fact, answer)
        self.assertIn("[D10]", answer)
        self.assertIn("[D11]", answer)

    def test_followup_next_step_keeps_rock_operational_facts(self):
        answer = draft_answer(
            "那下一步重点看什么？",
            [
                chunk("D09", "Rock下一步继续检查商品覆盖、订单集中和专辑结构，并结合品类运营动作安排专题陈列。"),
            ],
        )
        self.assertIsNotNone(answer)
        for fact in ("商品覆盖", "订单集中", "专辑结构", "专题陈列"):
            self.assertIn(fact, answer)
        self.assertIn("[D09]", answer)

    def test_fast_renderer_uses_the_fusion_answer_instead_of_single_excerpts(self):
        answer = local_answer(
            "第三季度客户分层结果与Rock运营动作如何结合？",
            [
                chunk("D04", "D04按第三季度金额阈值确定A、B、C层客户。"),
                chunk("D09", "D09的Rock动作重点是成交商品、专辑覆盖和专题陈列。"),
            ],
        )
        self.assertIsNotNone(answer)
        self.assertIn("先按D04的季度金额阈值确定A/B/C层", answer)


if __name__ == "__main__":
    unittest.main()
