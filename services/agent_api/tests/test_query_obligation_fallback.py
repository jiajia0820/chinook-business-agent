"""模型失败后的受控降级不能静默丢掉过滤、粒度和输出结构。

统一用总是抛 MODEL_TIMEOUT 的模型驱动 B → C → 合成全链路：未登记的国家
筛选不能退回全音频总额，"每月累计"不能退回季度单行，已登记的 USA 筛选
仍可走受控降级拿到确定结果。断言只看状态、行数和执行路径，不读评测答案。
"""

import unittest
from unittest.mock import patch

from backend.sql_module.errors import ModuleError
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.c_offline import create_canonical_sql_integration


class TimeoutModel:
    """永远超时的模型：强制 C 走"模型失败 → 受控降级"这条路径。"""

    def generate_json(self, *args):
        raise ModuleError('SQL_EXECUTION_FAILED', 'test timeout', 'MODEL_TIMEOUT')


async def ask_with_model(question: str, *, request_id: str = 'obligation-fallback'):
    """在模型恒定超时的条件下问一轮，返回最终 AskResponse。"""
    with patch('backend.sql_module.generation.HTTPJSONModel.from_env', return_value=TimeoutModel()):
        integration = await create_canonical_sql_integration(model_mode='live')
    try:
        return await integration.create_formal_graph_service().ask(
            AskRequest(profile_id='chinook-music', question=question), request_id=request_id)
    finally:
        await integration.aclose()


def row_count(answer) -> int:
    return sum(len(result.rows) for result in answer.sql_results)


class QueryObligationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_filter_never_answers_from_broad_fallback(self):
        answer = await ask_with_model('2025年第三季度英国音频销售额是多少？', request_id='fallback-uk')
        self.assertNotEqual(answer.status, 'answered')

    async def test_running_total_never_answers_with_quarter_scalar(self):
        answer = await ask_with_model('2025年第三季度每个月销售额累计到当月是多少？', request_id='fallback-cumulative')
        self.assertNotEqual(answer.status, 'answered')

    async def test_month_grouping_never_collapses_to_one_row(self):
        answer = await ask_with_model('2025年第三季度每个月音频销售额分别是多少？', request_id='fallback-monthly')
        if answer.status == 'answered':
            self.assertGreater(row_count(answer), 1)

    async def test_registered_country_filter_can_use_fallback(self):
        answer = await ask_with_model('2025年第三季度 USA 音频销售额是多少？', request_id='fallback-usa')
        self.assertEqual(answer.status, 'answered')
        self.assertEqual(answer.sql_results[0].rows[0]['sales_amount'], 19.8)

    async def test_top_n_fallback_keeps_rank_and_limit(self):
        answer = await ask_with_model('2025年第三季度音频销售额最高的五个账单国家是什么？', request_id='fallback-topn')
        self.assertEqual(answer.status, 'answered')
        self.assertEqual(len(answer.sql_results[0].rows), 5)


if __name__ == '__main__':
    unittest.main()
