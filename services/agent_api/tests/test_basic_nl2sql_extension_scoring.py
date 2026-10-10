"""Counterexamples for the five added cases and deferred live results."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as N
import unittest

from scripts.eval_scoring import compare_rows, judge
from scripts.stage2_eval import mark_pending_external

ROOT = Path(__file__).resolve().parents[3]
CASES = {c['id']: c for c in (json.loads(line) for line in
         (ROOT/'eval/agent/eval_basic_nl2sql.jsonl').read_text(encoding='utf-8').splitlines())}


def response(case):
    expected = case['gold_result']
    result = N(status='success', truncated=False, rows=deepcopy(expected if isinstance(expected,list) else [expected]))
    payload = {key: None for key in case['interface_expectation']['required_response_fields']}
    period = None if case['id'].endswith('011') else N(start='2025-01-01' if case['id'].endswith('012') else '2025-07-01',
             end='2026-01-01' if case['id'].endswith('012') else '2025-10-01', end_inclusive=False)
    return N(**(payload | dict(status='answered',route='sql',answer=case['gold_answer'],
             sql_results=[result],calculations=[],documents=[],limitations=[],error=None,
             clarification=None,time_range=period)))


class BasicExtensionScoringTests(unittest.TestCase):
    def test_all_five_valid_responses_pass(self):
        for number in range(11,16):
            case=CASES[f'basic-nl2sql-{number:03}']
            self.assertEqual(judge(case,response(case)),[],case['id'])

    def test_month_and_media_order_are_strict(self):
        for number in (12,15):
            case=CASES[f'basic-nl2sql-{number:03}'];actual=response(case)
            actual.sql_results[0].rows.reverse()
            self.assertTrue(compare_rows(case,actual))

    def test_equal_amount_customer_ids_cannot_be_swapped(self):
        case=CASES['basic-nl2sql-014'];actual=response(case)
        actual.sql_results[0].rows[:2]=actual.sql_results[0].rows[1::-1]
        self.assertTrue(compare_rows(case,actual))

    def test_missing_month_and_wrong_order_count_fail(self):
        case=CASES['basic-nl2sql-012'];actual=response(case)
        actual.sql_results[0].rows.pop()
        self.assertTrue(compare_rows(case,actual))
        actual=response(case);actual.sql_results[0].rows[0]['order_count']=7.01
        self.assertTrue(compare_rows(case,actual))

    def test_zero_missing_null_and_one_cent_fail(self):
        case=CASES['basic-nl2sql-015']
        for wrong in (None,0.01,False):
            actual=response(case);actual.sql_results[0].rows[2]['sales_amount']=wrong
            self.assertTrue(compare_rows(case,actual),wrong)
        actual=response(case);actual.sql_results[0].rows.pop()
        self.assertTrue(compare_rows(case,actual))

    def test_wrong_identity_column_and_truncated_result_fail(self):
        case=CASES['basic-nl2sql-011'];actual=response(case)
        actual.sql_results[0].rows[0]['TrackId']=1793
        self.assertTrue(judge(case,actual))
        actual=response(case);del actual.sql_results[0].rows[0]['genre']
        self.assertTrue(judge(case,actual))
        actual=response(case);actual.sql_results[0].truncated=True
        self.assertTrue(judge(case,actual))

    def test_wrong_year_route_and_missing_interface_field_fail(self):
        case=CASES['basic-nl2sql-011'];actual=response(case)
        actual.time_range=N(start='1791-01-01',end='1792-01-01',end_inclusive=False)
        self.assertTrue(judge(case,actual))
        actual=response(case);actual.route='rag'
        self.assertTrue(judge(case,actual))
        actual=response(case);del actual.profile_id
        self.assertTrue(judge(case,actual))

    def test_original_tie_scoring_is_preserved(self):
        case=CASES['basic-nl2sql-008'];actual=response(case)
        actual.sql_results[0].rows.reverse()
        self.assertEqual(compare_rows(case,actual),[])

    def test_deferred_or_failed_external_call_is_never_live_pass(self):
        for event in ({'event':'deferred_external_api','status':'not_called'},
                      {'event':'model_attempt','status':'error','reason':'MODEL_TIMEOUT'}):
            report={'total':1,'passed':1,'results':[{'id':'basic-nl2sql-001','passed':True,'problems':[]}]}
            mark_pending_external(report,[event|{'request_id':'eval-basic-nl2sql-001'}])
            self.assertEqual(report['passed'],0)
            self.assertEqual(report['business_passed'],1)

    def test_successful_retry_keeps_pass(self):
        report={'total':1,'passed':1,'results':[{'id':'basic-nl2sql-001','passed':True,'problems':[]}]}
        calls=[{'request_id':'eval-basic-nl2sql-001','event':'model_attempt','status':'error','reason':'MODEL_TIMEOUT'},
               {'request_id':'eval-basic-nl2sql-001','event':'model_attempt','status':'success'}]
        mark_pending_external(report,calls)
        self.assertEqual(report['passed'],1)


if __name__ == '__main__':
    unittest.main()
