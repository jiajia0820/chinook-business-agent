"""Contract tests for the v0.3 executable models."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from services.agent_api.app.contracts import AskRequest, AskResponse
from services.agent_api.app.contracts.models import response_json_schema


def base_response() -> dict:
    return {
        "request_id": "req-001",
        "session_id": "session-001",
        "profile_id": "chinook-ops",
        "status": "clarification_required",
        "answer": None,
        "route": "clarification",
        "intent": {"name": "query", "confidence": None},
        "entities": [],
        "time_range": None,
        "sql_results": [],
        "documents": [],
        "calculations": [],
        "metric_definitions": [],
        "limitations": [],
        "clarification": {
            "question": "请补充统计时间范围。",
            "missing_slots": [
                {"name": "time_range", "description": "增长率统计时间范围"}
            ],
            "options": ["按月", "按季度", "按年"],
        },
        "trace": [],
        "error": None,
    }


class AskRequestTests(unittest.TestCase):
    def test_defaults_match_contract(self) -> None:
        request = AskRequest(question="销售增长率是多少？", profile_id="chinook-ops")

        self.assertTrue(request.options.show_trace)
        self.assertEqual(request.options.max_rows, 50)
        self.assertEqual(request.options.top_k, 5)

    def test_blank_question_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AskRequest(question="   ", profile_id="chinook-ops")

    def test_option_limits_are_enforced(self) -> None:
        with self.assertRaises(ValidationError):
            AskRequest(
                question="查询销售额",
                profile_id="chinook-ops",
                options={"max_rows": 201, "top_k": 11},
            )


class AskResponseTests(unittest.TestCase):
    def test_contract_example_is_valid(self) -> None:
        response = AskResponse.model_validate(base_response())

        self.assertEqual(response.status, "clarification_required")
        self.assertEqual(response.route, "clarification")

    def test_answered_requires_answer(self) -> None:
        payload = base_response()
        payload.update(
            status="answered",
            route="sql",
            clarification=None,
        )

        with self.assertRaises(ValidationError):
            AskResponse.model_validate(payload)

    def test_unsupported_requires_matching_route(self) -> None:
        payload = base_response()
        payload.update(
            status="unsupported",
            route="rag",
            clarification=None,
            limitations=["当前 profile 不支持该能力。"],
        )

        with self.assertRaises(ValidationError):
            AskResponse.model_validate(payload)

    def test_unknown_fields_are_rejected(self) -> None:
        payload = base_response()
        payload["unexpected"] = True

        with self.assertRaises(ValidationError):
            AskResponse.model_validate(payload)

    def test_response_schema_can_be_generated(self) -> None:
        schema = response_json_schema()

        self.assertEqual(schema["title"], "AskResponse")
        self.assertIn("status", schema["properties"])
        self.assertIn("sql_results", schema["properties"])

    def test_future_error_codes_remain_compatible(self) -> None:
        payload = base_response()
        payload.update(
            status="error",
            route="unsupported",
            clarification=None,
            error={
                "code": "FUTURE_COMPATIBLE_ERROR",
                "message": "测试扩展错误码",
                "retryable": False,
                "details": None,
            },
        )

        response = AskResponse.model_validate(payload)

        self.assertEqual(response.error.code, "FUTURE_COMPATIBLE_ERROR")

    def test_sql_output_accepts_optional_execution_details(self) -> None:
        payload = base_response()
        payload.update(
            status="answered",
            route="sql",
            answer="Rock 音乐销售额为模拟值。",
            clarification=None,
            sql_results=[
                {
                    "query_id": "sql-001",
                    "profile_id": "chinook-ops",
                    "status": "success",
                    "columns": ["sales_amount"],
                    "rows": [{"sales_amount": 123.45}],
                    "row_count": 1,
                    "truncated": False,
                    "execution_ms": 8,
                    "source": {
                        "type": "database",
                        "name": "chinook",
                        "tables": ["Invoice", "InvoiceLine"],
                    },
                    "sql": "SELECT 123.45 AS sales_amount",
                    "params": {},
                    "error": None,
                }
            ],
        )

        response = AskResponse.model_validate(payload)

        self.assertEqual(response.sql_results[0].sql, "SELECT 123.45 AS sales_amount")
        self.assertEqual(response.sql_results[0].params, {})


if __name__ == "__main__":
    unittest.main()
