import hashlib
import sqlite3
import unittest
from backend.chinook_c import Chinook


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.db = Chinook()
        self.digest = hashlib.sha256(self.db.path.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(self.digest, hashlib.sha256(self.db.path.read_bytes()).hexdigest())

    def query(self, sql, **kwargs):
        return self.db.query(dict(query_id='test', sql=sql, **kwargs))

    def test_exact_tool_contract(self):
        r = self.query('SELECT COUNT(*) AS customer_count FROM Customer')
        self.assertEqual(set(r), {'query_id','status','sql','columns','rows','row_count','truncated','execution_ms','error'})
        self.assertEqual(r['rows'], [{'customer_count':59}])
        self.assertEqual(r['status'], 'success')
        self.assertIsNone(r['error'])

    def test_rejections(self):
        for sql in ['DELETE FROM Genre','DROP TABLE Genre',"ATTACH DATABASE ':memory:' AS other",'WITH x AS (SELECT 1) DELETE FROM Genre','SELECT 1; DELETE FROM Genre',"SELECT load_extension('x')",'PRAGMA user_version=1']:
            with self.subTest(sql=sql):
                r=self.query(sql)
                self.assertEqual(r['status'],'rejected')
                self.assertEqual(r['error']['code'],'SQL_READ_ONLY_VIOLATION')

    def test_invalid_inputs(self):
        for request in [None, [], {}, {'query_id':'x','sql':'SELECT 1','params':[]}, {'query_id':'x','sql':'SELECT 1','max_rows':True}, {'query_id':'x','sql':'SELECT 1','max_rows':201}, {'query_id':'x','sql':'SELECT 1','timeout_ms':0}]:
            self.assertEqual(self.db.query(request)['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.query('SELECT :x')['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.query('SELECT 1 AS a, 2 AS a')['status'],'rejected')

    def test_timeout_and_failure(self):
        r=self.query('WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x', timeout_ms=5)
        self.assertEqual(r['status'],'failed')
        self.assertIn('超时',r['error']['message'])
        self.assertEqual(self.query('SELECT MissingColumn FROM Genre')['status'],'failed')

    def test_params_truncation_and_empty(self):
        self.assertEqual(self.query('SELECT Name FROM Genre WHERE Name=:name',params={'name':"Rock' OR 1=1 --"})['rows'],[])
        r=self.query('SELECT TrackId FROM Track ORDER BY TrackId',max_rows=3)
        self.assertEqual(r['row_count'],3)
        self.assertTrue(r['truncated'])
        self.assertEqual(r['rows'][0],{'TrackId':1})

    def test_core_questions(self):
        r=self.db.ask({'question':'2025年Rock音乐销售额是多少？','session_id':'demo'})
        self.assertEqual(r['status'],'answered')
        self.assertEqual(r['session_id'],'demo')
        self.assertEqual(r['data_scope'],'music')
        self.assertEqual(r['sql_results'][0]['rows'][0]['value'],174.24)
        self.assertIn('MediaType',r['sql_results'][0]['source']['tables'])
        r=self.db.ask('Rock达到第三季度经营目标了吗？')
        self.assertEqual((r['status'],r['route']),('insufficient_evidence','cross_source'))
        self.assertEqual(r['documents'],[])
        r=self.db.ask('销售增长率是多少？')
        self.assertEqual(r['status'],'clarification_required')
        self.assertTrue(r['clarification']['question'])

    def test_music_vs_all_and_no_data(self):
        music=self.db.ask('2025年音乐销售额是多少？')
        all_data=self.db.ask('2025年销售额是多少？')
        with self.db.connect() as c:
            expected=c.execute("SELECT ROUND(SUM(UnitPrice*Quantity),2) FROM InvoiceLine WHERE InvoiceId IN (SELECT InvoiceId FROM Invoice WHERE InvoiceDate >= '2025-01-01' AND InvoiceDate < '2026-01-01') AND TrackId IN (SELECT TrackId FROM Track WHERE MediaTypeId IN (1,2,4,5))").fetchone()[0]
        self.assertEqual(music['sql_results'][0]['rows'][0]['value'],expected)
        self.assertLess(expected,all_data['sql_results'][0]['rows'][0]['value'])
        self.assertEqual(self.db.ask('2030年Rock销售额是多少？')['status'],'insufficient_evidence')

    def test_options_and_unsupported(self):
        r=self.db.ask({'question':'有哪些音乐类型？','options':{'max_rows':2,'show_trace':False}})
        self.assertEqual(r['trace'],[])
        self.assertEqual(r['sql_results'][0]['row_count'],2)
        self.assertEqual(self.db.ask({'question':'客户数量是多少？','options':{'max_rows':201}})['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.db.ask('2025年美国销售额是多少？')['route'],'unsupported')


if __name__=='__main__':
    unittest.main()
