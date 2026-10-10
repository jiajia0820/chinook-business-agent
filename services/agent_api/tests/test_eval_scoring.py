import unittest
from types import SimpleNamespace as N

from scripts.eval_scoring import judge, compare_rows, facts_missing, number_equal


def response(**overrides):
    return N(**(dict(status='answered', route='rag', answer='回答', calculations=[], sql_results=[],
        documents=[], limitations=[], clarification=None, error=None) | overrides))


class ScoringTests(unittest.TestCase):
    def test_percent_space_and_formula_rhs(self):
        self.assertEqual(facts_missing({'rate': '112.86/120=94.05%'}, response(answer='达成率94.05 %')), [])
        self.assertTrue(facts_missing({'rate': '112.86/120=94.05%'}, response(answer='实际112.86，目标120')))
        self.assertTrue(facts_missing({'rate': '94.05%'}, response(answer='94.05美元')))

    def test_counts_exact_money_tolerance(self):
        self.assertFalse(number_equal(8, 8.009, integer=True))
        self.assertTrue(number_equal(1.99, 2.00))
        self.assertFalse(number_equal(True, 1))

    def test_unordered_multiset_keeps_duplicates(self):
        case = {'gold_result': [{'count': 2}, {'count': 1}]}
        r = response(sql_results=[N(status='success', rows=[{'count': 1}, {'count': 2}])])
        self.assertEqual(compare_rows(case, r), [])
        case['gold_result'] = [{'count': 2}, {'count': 2}]
        self.assertTrue(compare_rows(case, r))

    def test_expected_clarification_is_success(self):
        case = {'id': 'session-003-t1', 'interface_expectation': {'expected_status': 'clarification_required',
            'expected_clarification': {'missing_slots': ['required_context']}}}
        self.assertEqual(judge(case, response(status='clarification_required', clarification=N(missing_slots=[N(name='year')]))), [])

    def test_document_id_does_not_substitute_for_answer(self):
        case = {'id': 'basic-rag-010', 'gold_answer': '2025年10月13日18:00。', 'expected_documents': ['D11']}
        self.assertTrue(judge(case, response(answer='见D11', documents=[N(doc_id='D11')])))

    def test_dates_ids_not_business_numbers(self):
        self.assertTrue(facts_missing({'actual': 13}, response(answer='D13，Genre:13，2025年10月13日')))

    def test_nested_month_must_match_its_period(self):
        r = response(sql_results=[N(status='success', rows=[{'month': '2025-07', 'sales_amount': 37.62}])])
        self.assertTrue(facts_missing({'monthly': {'2025-08': 37.62}}, r))

    def test_equivalent_route_reported_separately(self):
        case = {'id': 'test', 'gold_result': {'sales_amount': 2}, 'interface_expectation': {'expected_status': 'answered', 'expected_route': 'sql'}}
        r = response(route='cross_source', sql_results=[N(status='success', rows=[{'sales_amount': 2}])])
        self.assertEqual(judge(case, r), [])


if __name__ == '__main__':
    unittest.main()
