import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from backend.sql_module import Registry, AccessContext, SchemaService
from backend.sql_module.profiles import validate_profile, read_json
from backend.sql_module.schema import reflect
from backend.sql_module.errors import ModuleError, database_error

ROOT = Path(__file__).resolve().parents[1]


def profile(path, name='fixture', tables=None):
    return validate_profile(dict(profile_id=name, mode='sql_only', config_version='1',
        database=dict(dialect='sqlite', sqlite_path=str(path), display_name='测试夹具',
                      allowed_schemas=['main'], allowed_tables=tables or ['main.Parent', 'main.Child'])), ROOT)


class ConfigurationTests(unittest.TestCase):
    def test_strict_config(self):
        original = profile(ROOT / 'absent.db').data
        mutations = [lambda d: d.update(extra=1), lambda d: d.update(mode='bad'),
                     lambda d: d['database'].update(connect_timeout_seconds=True),
                     lambda d: d['database'].update(allowed_tables=['other.X']),
                     lambda d: d['database'].update(allowed_tables=[]),
                     lambda d: d['database'].update(url_env='SECRET'),
                     lambda d: d['database'].update(dialect='mysql'),
                     lambda d: d.update(config_version=1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(original)
                mutate(data)
                with self.assertRaises(ModuleError):
                    validate_profile(data, ROOT)

    def test_duplicate_json_and_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.json'
            path.write_text('{"x":1,"x":2}', encoding='utf-8')
            with self.assertRaises(ModuleError):
                read_json(path)
        p = profile(ROOT / 'absent.db')
        with self.assertRaises(ModuleError):
            Registry([p, p])

    def test_unknown_denied_missing(self):
        p = profile(ROOT / 'absent.db')
        registry = Registry([p])
        with self.assertRaises(ModuleError) as error:
            registry.resolve('unknown', AccessContext.local(registry))
        self.assertEqual(error.exception.code, 'PROFILE_NOT_FOUND')
        with self.assertRaises(ModuleError) as error:
            registry.resolve(p.id, AccessContext({}))
        self.assertEqual(error.exception.reason, 'ACCESS_DENIED')
        with self.assertRaises(ModuleError) as error:
            p.connection_url()
        self.assertEqual(error.exception.reason, 'DATABASE_MISSING')

    def test_url_and_no_sql(self):
        data = copy.deepcopy(profile(ROOT / 'absent.db').data)
        data['database'].pop('sqlite_path')
        data['database']['url_env'] = 'TEST_C_SECRET'
        p = validate_profile(data, ROOT)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModuleError) as error:
                p.connection_url()
            self.assertEqual(error.exception.reason, 'CONFIG_MISSING')
        with patch.dict(os.environ, {'TEST_C_SECRET': 'postgresql://user:password@host/db'}):
            with self.assertRaises(ModuleError) as error:
                p.connection_url()
            self.assertNotIn('password', str(error.exception))
        p = validate_profile({'profile_id':'rag', 'mode':'rag_only', 'config_version':'1'}, ROOT)
        registry = Registry([p])
        with self.assertRaises(ModuleError) as error:
            registry.resolve('rag', AccessContext.local(registry))
        self.assertEqual(error.exception.code, 'UNSUPPORTED_CAPABILITY')

    def test_connection_failure_is_sanitized(self):
        registry = Registry.load()
        with patch('backend.sql_module.adapters.sqlite_engine', side_effect=RuntimeError('secret-password')):
            with self.assertRaises(ModuleError) as error:
                SchemaService(registry).get_schema('chinook', AccessContext.local(registry))
        self.assertEqual(error.exception.reason, 'CONNECTION_FAILED')
        self.assertNotIn('secret-password', str(error.exception))
        exc = RuntimeError('private')
        exc.sqlstate = '28P01'
        self.assertEqual(database_error(exc).reason, 'AUTHENTICATION_FAILED')

    def test_invalid_enum_types_and_snapshot(self):
        original = profile(ROOT / 'absent.db').data
        for key in ('mode', 'database'):
            for value in (None, [], {}, True):
                data = copy.deepcopy(original)
                data[key] = value
                with self.assertRaises(ModuleError):
                    validate_profile(data, ROOT)
        p = validate_profile(original, ROOT)
        original['database']['allowed_tables'].clear()
        self.assertTrue(p.database['allowed_tables'])


class SQLiteFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / '测试.db'
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript('CREATE TABLE Parent(a INTEGER, b TEXT, PRIMARY KEY(b,a));'
                'CREATE TABLE Child(x TEXT, y INTEGER, UNIQUE(x,y), FOREIGN KEY(x,y) REFERENCES Parent(b,a));')
        self.registry = Registry([profile(self.path)])
        self.access = AccessContext.local(self.registry)

    def tearDown(self):
        self.tmp.cleanup()

    def test_composite_and_no_write(self):
        before = self.path.read_bytes()
        schema = SchemaService(self.registry).get_schema('fixture', self.access)
        self.assertEqual(next(t for t in schema['tables'] if t['name']=='Parent')['primary_key'], ['b','a'])
        self.assertEqual(schema['foreign_keys'][0]['from_columns'], ['x','y'])
        self.assertEqual(schema['foreign_keys'][0]['to_columns'], ['b','a'])
        self.assertEqual(schema['tables'][0]['unique_constraints'][0]['columns'], ['x','y'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(schema, SchemaService(self.registry).get_schema('fixture', self.access))

    def test_access_isolation_and_version(self):
        service = SchemaService(self.registry)
        full = service.get_schema('fixture', self.access)
        narrow = service.get_schema('fixture', AccessContext({'fixture': {'main.Child'}}))
        self.assertEqual([t['table_id'] for t in narrow['tables']], ['main.Child'])
        self.assertEqual(narrow['foreign_keys'], [])
        self.assertNotEqual(full['schema_version'], narrow['schema_version'])
        self.assertNotIn('Parent', json.dumps(narrow))

    def test_profiles_concurrent_isolation(self):
        other = Path(self.tmp.name) / 'other.db'
        with closing(sqlite3.connect(other)) as conn:
            conn.execute('CREATE TABLE OnlyOther(id TEXT PRIMARY KEY)')
        registry = Registry([profile(self.path), profile(other, 'other', ['main.OnlyOther'])])
        service, access = SchemaService(registry), AccessContext.local(registry)
        ids = ['fixture','other'] * 4
        with ThreadPoolExecutor(max_workers=4) as executor:
            snapshots = list(executor.map(lambda p: service.get_schema(p, access), ids))
        for pid, snapshot in zip(ids, snapshots):
            self.assertEqual(snapshot['profile_id'], pid)
            self.assertEqual(len(snapshot['tables']), 2 if pid=='fixture' else 1)

    def test_implicit_composite_reference(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('CREATE TABLE Implicit(x TEXT,y INTEGER,FOREIGN KEY(x,y) REFERENCES Parent)')
        registry = Registry([profile(self.path, tables=['main.Parent', 'main.Implicit'])])
        snapshot = SchemaService(registry).get_schema('fixture', AccessContext.local(registry))
        self.assertEqual(snapshot['foreign_keys'][0]['to_columns'], ['b','a'])


class MetadataMockTests(unittest.TestCase):
    def test_cross_schema_same_name_composite(self):
        data = dict(profile_id='pg', mode='sql_only', config_version='1', database=dict(
            dialect='postgresql', url_env='PG_SECRET', display_name='PG mock', allowed_schemas=['Sales','Production'],
            allowed_tables=['Sales.Item','Production.Item']))
        p = validate_profile(data, ROOT)
        inspector = Mock()
        inspector.get_schema_names.return_value = ['Sales','Production']
        inspector.get_table_names.return_value = ['Item']
        inspector.get_columns.return_value = [dict(name='a', type='INTEGER', nullable=False),dict(name='b',type='TEXT',nullable=True)]
        inspector.get_pk_constraint.return_value = {'constrained_columns':['b','a']}
        inspector.get_table_comment.return_value = {'text':None}
        inspector.get_unique_constraints.return_value = []
        inspector.get_foreign_keys.side_effect = lambda name, schema, **kw: [] if schema=='Production' else [dict(
            name='fk', constrained_columns=['a','b'], referred_schema='Production', referred_table='Item', referred_columns=['a','b'])]
        snapshot = reflect(p, set(p.database['allowed_tables']), inspector, 'mock')
        self.assertEqual(len(snapshot['tables']), 2)
        self.assertEqual(snapshot['foreign_keys'][0]['to_table'], 'Production.Item')
        self.assertEqual(snapshot['foreign_keys'][0]['to_columns'], ['a','b'])
        self.assertTrue(all(c.kwargs.get('postgresql_ignore_search_path') for c in inspector.get_foreign_keys.call_args_list))


class RealIntegrationTests(unittest.TestCase):
    def test_real_chinook_independent_pragma(self):
        path = ROOT / 'data/chinook/Chinook.db'
        before = hashlib.sha256(path.read_bytes()).digest()
        registry = Registry.load()
        schema = SchemaService(registry).get_schema('chinook', AccessContext.local(registry))
        self.assertEqual(len(schema['tables']), 11)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
            for table in schema['tables']:
                raw = list(conn.execute('PRAGMA table_info("'+table['name']+'")'))
                self.assertEqual([c['name'] for c in table['columns']], [r[1] for r in raw])
                self.assertEqual(table['primary_key'], [r[1] for r in sorted(raw,key=lambda r:r[5]) if r[5]])
        self.assertIn(dict(name=None,from_table='main.InvoiceLine',from_columns=['InvoiceId'],
                           to_table='main.Invoice',to_columns=['InvoiceId']), schema['foreign_keys'])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())

    def test_real_adventureworks(self):
        directory = os.environ.get('C_ADVENTUREWORKS_PROFILES_DIR')
        if not directory:
            self.skipTest('未配置 C_ADVENTUREWORKS_PROFILES_DIR；AdventureWorks 真实集成未验证')
        registry = Registry.load(directory)
        schema = SchemaService(registry).get_schema('adventureworks', AccessContext.local(registry))
        self.assertEqual(schema['database']['dialect'], 'postgresql')
        self.assertTrue(schema['tables'])
        self.assertTrue(schema['database']['server_version'])


class CLITests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable,'-m','backend.sql_module',*args],cwd=ROOT,
                              capture_output=True,encoding='utf-8')

    def test_cli_commands_export_and_error(self):
        for args in [('profiles',),('check','chinook'),('tables','chinook'),
                     ('table','chinook','main.PlaylistTrack'),('foreign-keys','chinook')]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode,0,result.stderr)
            json.loads(result.stdout)
        result = self.run_cli('check','unknown')
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stderr)['error']['code'],'PROFILE_NOT_FOUND')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '结构.json'
            result = self.run_cli('export-schema','chinook','--output',str(path))
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['profile_id'],'chinook')
            before = path.read_bytes()
            self.assertEqual(self.run_cli('export-schema','unknown','--output',str(path)).returncode,1)
            self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
