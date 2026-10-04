from contextlib import closing
from datetime import datetime, date
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from backend.sql_module import Registry, AccessContext
from backend.sql_module.execution import SQLExecutor, serialize
from backend.sql_module.validation import validate_sql
from backend.sql_module.errors import ModuleError
from backend.sql_module.profiles import validate_profile

ROOT = Path(__file__).resolve().parents[1]


class SQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load()
        cls.access = AccessContext.local(cls.registry)
        cls.executor = SQLExecutor(cls.registry)
        cls.path = ROOT/'data/chinook/Chinook.db'
        cls.before = hashlib.sha256(cls.path.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert cls.before == hashlib.sha256(cls.path.read_bytes()).hexdigest()

    def run_sql(self, sql, **extra):
        return self.executor.execute_sql(dict(query_id='test',profile_id='chinook',sql=sql,**extra),self.access)

    def test_v03_success(self):
        result = self.run_sql('SELECT COUNT(*) AS customer_count FROM Customer')
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(set(result), {'query_id','profile_id','status','columns','rows','row_count','truncated','execution_ms','source','error'})
        self.assertEqual(result['rows'],[{'customer_count':59}])
        self.assertEqual(result['source']['tables'],['main.Customer'])

    def test_dangerous_queries(self):
        sqls = ['DELETE FROM Customer','DROP TABLE Genre','SELECT 1; SELECT 2',
                'WITH d AS (DELETE FROM Customer RETURNING *) SELECT * FROM d',
                'SELECT * INTO temp_copy FROM Customer', 'SELECT load_extension(:x)',
                "SELECT readfile('secret')", 'PRAGMA user_version', "ATTACH DATABASE ':memory:' AS x",
                'SELECT * FROM sqlite_master','SELECT * FROM other.Customer','SELECT * FROM main.Customer FOR UPDATE',
                'SELECT pg_read_file(:x)', "SELECT CAST('x' AS regclass)",
                'SELECT * FROM Customer UNION SELECT * FROM sqlite_master']
        for sql in sqls:
            with self.subTest(sql=sql):
                result = self.run_sql(sql, params={'x':'secret'} if ':x' in sql else {})
                self.assertEqual(result['status'],'rejected',result)
                self.assertEqual(result['error']['code'],'SQL_READ_ONLY_VIOLATION',result)
                self.assertEqual(result['rows'],[])

    def test_cte_subquery_union_and_aliases(self):
        queries = [
            ('WITH c AS (SELECT CustomerId FROM Customer) SELECT COUNT(*) AS n FROM c',59,['main.Customer']),
            ('SELECT COUNT(*) AS n FROM (SELECT CustomerId FROM Customer WHERE Country=:country) AS x',13,['main.Customer']),
            ('WITH Customer AS (SELECT 1 AS id) SELECT id AS n FROM Customer',1,[]),
            ('SELECT COUNT(*) AS n FROM Customer c WHERE EXISTS (SELECT 1 FROM Invoice i WHERE i.CustomerId=c.CustomerId)',59,['main.Customer','main.Invoice']),
            ('SELECT 1 AS n UNION ALL SELECT 2 AS n',1,[])]
        for sql,n,tables in queries:
            with self.subTest(sql=sql):
                r=self.run_sql(sql,params={'country':'USA'} if ':country' in sql else {})
                self.assertEqual(r['status'],'success',r)
                self.assertEqual(r['rows'][0]['n'],n)
                self.assertEqual(r['source']['tables'],tables)

    def test_columns_and_scope_rejections(self):
        for sql in ['SELECT MissingColumn FROM Genre', 'SELECT CustomerId FROM Customer c JOIN Invoice i ON c.CustomerId=i.CustomerId',
                    'SELECT c.Missing FROM Customer c', 'SELECT Secret FROM (SELECT Name FROM Genre) x']:
            with self.subTest(sql=sql):
                r=self.run_sql(sql)
                self.assertEqual(r['status'],'rejected',r)
                self.assertEqual(r['error']['details']['reason'],'COLUMN_DENIED')

    def test_params_quotes_colons_and_percent(self):
        r=self.run_sql("SELECT Name FROM Genre WHERE Name=:name",params={'name':"Rock' OR 1=1 --"})
        self.assertEqual(r['status'],'success',r)
        self.assertEqual(r['rows'],[])
        r=self.run_sql("SELECT ':not_a_bind' AS a, '100%' AS b, :p AS c",params={'p':True})
        self.assertEqual(r['status'],'success',r)
        self.assertEqual(r['rows'][0]['a'],':not_a_bind')
        for params in ({'x':1},{'p':float('nan')},{'p':[]},{'bad-name':1}):
            self.assertEqual(self.run_sql('SELECT :p AS p',params=params)['error']['code'],'INVALID_REQUEST')

    def test_invalid_requests_and_duplicates(self):
        for request in [None,[],{},dict(query_id='x',profile_id='chinook',sql='SELECT 1',max_rows=True),
                        dict(query_id='x',profile_id='chinook',sql='SELECT 1',unexpected=True)]:
            self.assertEqual(self.executor.execute_sql(request,self.access)['error']['code'],'INVALID_REQUEST')
        self.assertEqual(self.run_sql('SELECT 1 AS x, 2 AS x')['error']['code'],'INVALID_REQUEST')
        r=self.executor.execute_sql(dict(query_id='x',profile_id='unknown',sql='SELECT 1'),self.access)
        self.assertEqual(r['error']['code'],'PROFILE_NOT_FOUND')

    def test_truncation_exact_empty_and_explicit_limit(self):
        r=self.run_sql('SELECT GenreId FROM Genre ORDER BY GenreId',max_rows=3)
        self.assertEqual((r['row_count'],r['truncated']),(3,True),r)
        for sql,count in [('SELECT GenreId FROM Genre ORDER BY GenreId LIMIT 3',3),('SELECT GenreId FROM Genre WHERE GenreId<0',0)]:
            r=self.run_sql(sql,max_rows=3)
            self.assertEqual(r['status'],'success',r)
            self.assertEqual((r['row_count'],r['truncated']),(count,False))

    def test_timeout_then_recover(self):
        r=self.run_sql('SELECT SUM(a.TrackId+b.TrackId+c.TrackId) AS n FROM Track a CROSS JOIN Track b CROSS JOIN Track c',timeout_ms=1)
        self.assertEqual(r['status'],'failed',r)
        self.assertEqual(r['error']['details']['reason'],'TIMEOUT',r)
        self.assertEqual(self.run_sql('SELECT 1 AS n')['status'],'success')

    def test_cast_case_and_date_boundaries(self):
        for sql,expected in [("SELECT CAST(:x AS INTEGER) AS n",3),
                             ("SELECT CASE WHEN 1=1 THEN 3 ELSE 0 END AS n",3),
                             ("SELECT strftime('%Y','2025-01-02') AS n",'2025')]:
            r=self.run_sql(sql,params={'x':'3'} if ':x' in sql else {})
            self.assertEqual(r['status'],'success',r)
            self.assertEqual(r['rows'][0]['n'],expected)

    def test_authorization_scope(self):
        access=AccessContext({'chinook':{'main.Genre'}})
        request=dict(query_id='x',profile_id='chinook',sql='SELECT * FROM Customer')
        r=self.executor.execute_sql(request,access)
        self.assertEqual(r['error']['details']['reason'],'TABLE_DENIED',r)


class AdditionalTests(unittest.TestCase):
    def test_serialization(self):
        self.assertEqual(serialize(Decimal('1.2300')),'1.2300')
        self.assertEqual(serialize(date(2025,1,2)),'2025-01-02')
        self.assertEqual(serialize(datetime(2025,1,2,3,4,5)),'2025-01-02T03:04:05')
        self.assertIsNone(serialize(None))
        for v in (float('inf'), Decimal('NaN'), b'bytes'):
            with self.assertRaises(ModuleError): serialize(v)

    def test_pg_cross_schema_identifiers_and_percent_binding(self):
        snapshot=dict(database={'dialect':'postgresql'},tables=[dict(table_id=s+'.item',schema=s,name='item',columns=[dict(name='id',data_type='INTEGER')]) for s in ['sales','production']])
        with self.assertRaises(ModuleError): validate_sql('SELECT id FROM item',{},snapshot)
        q=validate_sql("SELECT a.id FROM sales.item a JOIN production.item b ON a.id=b.id WHERE a.id=:id AND '10%'='10%'",{'id':1},snapshot)
        self.assertEqual(q.sources,('production.item','sales.item'))
        self.assertIn('%(id)s',q.sql)
        self.assertIn('10%%',q.sql)
        with self.assertRaises(ModuleError): validate_sql("SELECT pg_catalog.pg_read_file('x')",{},snapshot)
        mixed=dict(database={'dialect':'postgresql'},tables=[dict(table_id='Sales.Order',schema='Sales',name='Order',columns=[dict(name='OrderId',data_type='INTEGER')])])
        q=validate_sql('SELECT "OrderId" FROM "Sales"."Order"',{},mixed)
        self.assertEqual(q.sources,('Sales.Order',))
        with self.assertRaises(ModuleError): validate_sql('SELECT orderid FROM sales.order',{},mixed)
        with self.assertRaises(ModuleError): validate_sql('SELECT orderid FROM "Sales"."Order"',{},mixed)

    def test_profile_policy_and_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'x.db'
            with closing(sqlite3.connect(path)) as c:
                c.executescript('CREATE TABLE T(id INTEGER); INSERT INTO T VALUES (1),(2),(3);')
            data=dict(profile_id='p',config_version='1',mode='sql_only',database=dict(dialect='sqlite',sqlite_path=str(path),display_name='夹具',allowed_schemas=['main'],allowed_tables=['main.T']),policy=dict(max_rows=1,query_timeout_ms=5000))
            registry=Registry([validate_profile(data,directory)])
            r=SQLExecutor(registry).execute_sql(dict(query_id='q',profile_id='p',sql='SELECT id FROM T ORDER BY id',max_rows=200),AccessContext.local(registry))
            self.assertEqual((r['row_count'],r['truncated']),(1,True),r)

    def test_real_pg_not_configured(self):
        directory=os.environ.get('C_ADVENTUREWORKS_PROFILES_DIR')
        if not directory: self.skipTest('AdventureWorks PostgreSQL 未部署/配置；真实安全执行未验证')
        registry=Registry.load(directory)
        r=SQLExecutor(registry).execute_sql(dict(query_id='q',profile_id='adventureworks',sql='SELECT 1 AS probe'),AccessContext.local(registry))
        self.assertEqual(r['status'],'success',r)


if __name__=='__main__': unittest.main()
