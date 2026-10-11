"""Reject numerically plausible intermediate-only cross-source answers."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as N
import unittest
from scripts.eval_scoring import workflow_assertions


class WorkflowScoringTests(unittest.TestCase):
    def setUp(self):
        expected=json.loads(Path('scripts/stage2_cross_assertions.json').read_text(encoding='utf-8'))
        records=[{'CustomerId':int(k),'all_audio_sales_amount':v} for k,v in expected['all_audio_amounts'].items()]
        self.response=N(sql_results=[N(rows=records,status='success')],
            answer='\n'.join(f'CustomerId {k}：全部音频 {expected["all_audio_amounts"][str(k)]:.2f} USD，≥12.00 USD 阈值，sql-cell:current。' for k in expected['eligible_ids']),
            documents=[N(doc_id='D04',text='A | 重点维护 | 大于等于 12.00 | 辅助观察')],
            time_range=N(start=expected['period'][0],end=expected['period'][1]))

    def test_rock_intermediate_cannot_replace_complete_audio_or_final_decisions(self):
        self.assertEqual(workflow_assertions({'id':'cross-004'},self.response),[])
        bad=copy.deepcopy(self.response)
        for row in bad.sql_results[0].rows:
            row['sales_amount']=row.pop('all_audio_sales_amount')
        self.assertTrue(workflow_assertions({'id':'cross-004'},bad))
        bad=copy.deepcopy(self.response);bad.answer='D04给出A层客户10、31、48。'
        self.assertTrue(workflow_assertions({'id':'cross-004'},bad))

    def test_wrong_value_period_and_extra_eligible_customer_are_rejected(self):
        for change in ['amount','period','extra','threshold']:
            bad=copy.deepcopy(self.response)
            if change=='amount':bad.sql_results[0].rows[0]['all_audio_sales_amount']=99
            elif change=='period':bad.time_range.start='2025-04-01'
            elif change=='extra':bad.answer+='\nCustomerId 1：全部音频 8.91 USD，≥12.00 USD 阈值，sql-cell:current。'
            else:bad.answer=bad.answer.replace('≥12.00','≥8.00')
            self.assertTrue(workflow_assertions({'id':'cross-004'},bad),change)


if __name__=='__main__':unittest.main()
