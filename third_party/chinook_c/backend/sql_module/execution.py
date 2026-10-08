from datetime import date, datetime, time as datetime_time
from decimal import Decimal
import math
import sqlite3
import time
from sqlalchemy.exc import DBAPIError
from .adapters import connection
from .schema import SchemaService
from .errors import ModuleError
from .profiles import obj, string, integer
from .validation import validate_sql, SAFE_FUNCTIONS, request_error


def serialize(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ModuleError('SQL_EXECUTION_FAILED', '结果包含非有限十进制值', 'SERIALIZATION_FAILED')
        return str(value)
    if isinstance(value, (date, datetime, datetime_time)):
        return value.isoformat()
    if type(value) is float and math.isfinite(value):
        return value
    raise ModuleError('SQL_EXECUTION_FAILED', '结果类型无法安全序列化', 'SERIALIZATION_FAILED')


def query_error(exc):
    original = getattr(exc, 'orig', exc)
    state = getattr(original, 'sqlstate', None)
    sqlite_code = getattr(original, 'sqlite_errorcode', None)
    if state == '57014' or sqlite_code == getattr(sqlite3, 'SQLITE_INTERRUPT', 9) or (isinstance(original, sqlite3.Error) and str(original)=='interrupted'):
        return ModuleError('SQL_EXECUTION_FAILED', 'SQL 查询超时', 'TIMEOUT')
    if state == '42501' or sqlite_code == getattr(sqlite3, 'SQLITE_AUTH', 23) or (isinstance(original, sqlite3.Error) and 'authorized' in str(original)):
        return ModuleError('SQL_READ_ONLY_VIOLATION', '数据库拒绝此查询', 'DATABASE_DENIED')
    return ModuleError('SQL_EXECUTION_FAILED', 'SQL 执行失败；请核对结构、参数与方言', 'DATABASE_ERROR')


def sqlite_guard(raw, snapshot, deadline):
    tables = {t['table_id'].casefold(): {c['name'].casefold() for c in t['columns']} for t in snapshot['tables']}
    allowed_functions = {n.lower() for n in SAFE_FUNCTIONS} | {'substr','ifnull'}
    def authorizer(action, arg1, arg2, database, trigger):
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            key = ((database or '')+'.'+(arg1 or '')).casefold()
            if key in tables and (not arg2 or arg2.casefold() in tables[key]):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() in allowed_functions:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    raw.set_authorizer(authorizer)
    raw.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)


class SQLExecutor:
    def __init__(self, registry):
        self.registry = registry
        self.schemas = SchemaService(registry)

    def execute_sql(self, request, access):
        start = time.monotonic()
        data = request if isinstance(request, dict) else {}
        result = dict(query_id=data.get('query_id'), profile_id=data.get('profile_id'), status='rejected',
                      columns=[], rows=[], row_count=0, truncated=False, execution_ms=0,
                      source=dict(type='database', name=None, tables=[]), error=None)
        def finish(error=None, status=None):
            result['execution_ms'] = round((time.monotonic()-start)*1000, 3)
            if error:
                result['error'] = error.as_dict()
                result['status'] = status or ('failed' if error.code in ('PROFILE_UNAVAILABLE','SQL_EXECUTION_FAILED','INTERNAL_ERROR') else 'rejected')
                result.update(columns=[], rows=[], row_count=0, truncated=False)
            return result
        try:
            obj(request, ['query_id','profile_id','sql','params','max_rows','timeout_ms'], ['query_id','profile_id','sql'], 'SQL 请求')
            string(data['query_id'], 'query_id')
            string(data['profile_id'], 'profile_id')
            integer(data.get('max_rows',50),1,200,'max_rows')
            integer(data.get('timeout_ms',5000),1,30000,'timeout_ms')
            profile, _ = self.registry.resolve(data['profile_id'], access)
            result['source']['name'] = profile.database['display_name']
            snapshot = self.schemas.get_schema(data['profile_id'], access)
            query = validate_sql(data['sql'], data.get('params',{}), snapshot)
            result['source']['tables'] = list(query.sources)
            policy = profile.data.get('policy',{})
            limit = min(data.get('max_rows',50), policy.get('max_rows',200))
            timeout = min(data.get('timeout_ms',5000), policy.get('query_timeout_ms',5000))
            # Database query budget starts after metadata/config validation; execution_ms includes both.
            with connection(profile, timeout_ms=timeout) as conn:
                deadline = time.monotonic() + timeout/1000
                raw = None
                try:
                    if profile.database['dialect']=='sqlite':
                        raw = conn.connection.driver_connection
                        sqlite_guard(raw, snapshot, deadline)
                    # PostgreSQL streams so fetchmany does not first buffer the whole result in the client.
                    run_conn = conn.execution_options(stream_results=True) if profile.database['dialect']=='postgresql' else conn
                    with run_conn.exec_driver_sql(query.sql, query.params) as cursor:
                        columns = list(cursor.keys())
                        if len(set(columns)) != len(columns):
                            raise request_error('结果列名重复；请使用唯一 AS 别名')
                        rows = cursor.fetchmany(limit+1)
                        if time.monotonic() >= deadline:
                            raise ModuleError('SQL_EXECUTION_FAILED', 'SQL 查询超时', 'TIMEOUT')
                        formatted = [{k:serialize(v) for k,v in zip(columns,row)} for row in rows[:limit]]
                    result.update(status='success', columns=columns, rows=formatted,
                                  row_count=len(formatted), truncated=len(rows)>limit)
                except DBAPIError as exc:
                    raise query_error(exc) from None
                finally:
                    if raw is not None:
                        raw.set_authorizer(None)
                        raw.set_progress_handler(None, 0)
            return finish()
        except ModuleError as exc:
            return finish(exc)
        except Exception:
            return finish(ModuleError('INTERNAL_ERROR', 'SQL 模块内部异常'))
