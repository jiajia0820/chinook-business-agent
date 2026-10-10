import json
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.sql_module.errors import ModuleError
from scripts.eval_scoring import judge
from services.agent_api.app.contracts import AskRequest
from services.agent_api.app.integrations.c_offline import create_canonical_sql_integration


class MultitableFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_plans_answer_full_multitable_group_without_gold_input(self):
        class UnavailableModel:
            def generate_json(self, *args):
                raise ModuleError('SQL_EXECUTION_FAILED', 'test timeout', 'MODEL_TIMEOUT')
        with patch('backend.sql_module.generation.HTTPJSONModel.from_env', return_value=UnavailableModel()):
            integration = await create_canonical_sql_integration(model_mode='live')
        try:
            service = integration.create_formal_graph_service()
            path = Path(__file__).resolve().parents[3]/'eval/agent/eval_special_multitable.jsonl'
            for line in path.read_text(encoding='utf-8').splitlines():
                case = json.loads(line)
                with self.subTest(case=case['id']):
                    # Only user question/profile enters the runtime. Gold stays
                    # in the independent evaluator after the response returns.
                    result = await service.ask(AskRequest(profile_id='chinook-music', question=case['question']), request_id='test-' + case['id'])
                    self.assertEqual(judge(case, result), [], msg=result.model_dump_json())
        finally:
            await integration.aclose()


if __name__ == '__main__':
    unittest.main()
