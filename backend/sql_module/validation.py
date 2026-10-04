"""Conservative AST policy. It is not a replacement for database permissions."""
from dataclasses import dataclass
import math
import re
import uuid
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.schema import MappingSchema
from .errors import ModuleError

SAFE_FUNCTIONS = frozenset('COUNT SUM AVG MIN MAX ROUND ABS COALESCE NULLIF LOWER UPPER LENGTH SUBSTRING '
    'TRIM LTRIM RTRIM CAST TRY_CAST CASE IF DATE DATETIME STRFTIME TIME_TO_STR TS_OR_DS_TO_TIMESTAMP '
    'TS_OR_DS_TO_DATE DATE_TRUNC EXTRACT'.split())
STRUCTURES = frozenset('Select Union Intersect Except Subquery With CTE Table TableAlias Column Identifier '
    'Literal Null Boolean Placeholder Star Alias From Join Where Group Having Order Ordered Limit Offset '
    'Distinct Paren And Or Not EQ NEQ GT GTE LT LTE Add Sub Mul Div Mod Neg Between In Like ILike Is '
    'DataType DataTypeParam Tuple Exists Var'.split())
CAST_TYPES = frozenset('INT BIGINT SMALLINT TINYINT FLOAT DOUBLE DECIMAL BOOLEAN TEXT VARCHAR CHAR '
                       'DATE DATETIME TIMESTAMP TIMESTAMPTZ REAL'.split())
PARAM_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def reject(message='SQL 超出允许的只读查询范围', reason='UNSUPPORTED_SQL'):
    return ModuleError('SQL_READ_ONLY_VIOLATION', message, reason)


def request_error(message):
    return ModuleError('INVALID_REQUEST', message)


def validate_params(params):
    if not isinstance(params, dict):
        raise request_error('params 必须为命名标量参数对象')
    for key, value in params.items():
        if (not isinstance(key, str) or not PARAM_NAME.fullmatch(key)
                or type(value) not in (str, int, float, bool, type(None))
                or (type(value) is float and not math.isfinite(value))):
            raise request_error('参数名称或标量类型无效；不允许非有限数值')


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    params: dict
    sources: tuple
    selected_columns: tuple


def validate_sql(sql, params, snapshot):
    validate_params(params)
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise request_error('sql 必须为非空文本且不超过 20000 字符')
    dialect = snapshot['database']['dialect']
    read = 'postgres' if dialect == 'postgresql' else 'sqlite'
    try:
        parsed = sqlglot.parse(sql, read=read)
    except sqlglot.errors.SqlglotError:
        raise reject('无法解析受支持的单条只读 SQL', 'PARSE_ERROR') from None
    if len(parsed) != 1 or not isinstance(parsed[0], (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise reject('只允许单条 SELECT 或只读 WITH 查询')
    tree = parsed[0]
    nodes = list(tree.walk())
    if len(nodes) > 4000:
        raise reject('SQL 结构过于复杂')
    for node in nodes:
        if type(node).__name__ in STRUCTURES:
            pass
        elif isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name()
            if name not in SAFE_FUNCTIONS:
                raise reject('SQL 使用了未允许的函数', 'FUNCTION_DENIED')
        else:
            raise reject('SQL 包含未支持的结构', 'STRUCTURE_DENIED')
        if isinstance(node, exp.With) and node.args.get('recursive'):
            raise reject('本阶段不支持递归 CTE')
        if isinstance(node, exp.Select) and any(node.args.get(k) for k in ('into', 'locks', 'hint', 'operation_modifiers')):
            raise reject('不允许写入、锁定或查询提示')
        if isinstance(node, exp.DataType):
            typename = getattr(node.this, 'value', str(node.this))
            if typename not in CAST_TYPES:
                raise reject('不允许该类型转换', 'TYPE_DENIED')
        if isinstance(node, exp.Placeholder) and (not node.name or not PARAM_NAME.fullmatch(node.name)):
            raise request_error('仅支持 :name 命名参数')
    parameter_names = {p.name for p in tree.find_all(exp.Placeholder)}
    if parameter_names != set(params):
        raise request_error('SQL 占位符与 params 必须完全匹配')
    tables = snapshot['tables']
    sources = set()
    scopes = traverse_scope(tree)
    if not scopes:
        raise reject('无法确定查询作用域')
    def identifier_value(identifier):
        if identifier is None:
            return None
        return identifier.name if read=='sqlite' or identifier.args.get('quoted') else identifier.name.lower()
    for scope in scopes:
        for source in scope.sources.values():
            if not isinstance(source, exp.Table):
                continue
            if source.catalog or not isinstance(source.this, exp.Identifier):
                raise reject('不允许跨库或外部对象', 'TABLE_DENIED')
            schema = identifier_value(source.args.get('db'))
            name = identifier_value(source.this)
            candidates = [t for t in tables if ((t['name'].casefold()==name.casefold()) if read=='sqlite' else t['name']==name)
                          and (schema is None or ((t['schema'].casefold()==schema.casefold()) if read=='sqlite' else t['schema']==schema))]
            if len(candidates) != 1:
                raise reject('表未授权、不存在或同名表需明确 Schema', 'TABLE_DENIED')
            actual = candidates[0]
            sources.add(actual['table_id'])
            source.set('this', exp.to_identifier(actual['name'], quoted=True))
            source.set('db', exp.to_identifier(actual['schema'], quoted=True))
    mapping = {}
    for table in tables:
        mapping.setdefault(table['schema'], {})[table['name']] = {c['name']: c['data_type'] for c in table['columns']}
    try:
        # PostgreSQL quoted names are case-sensitive. Raw dict schemas would normalize them to lowercase.
        schema_mapping = MappingSchema(mapping, dialect=read, normalize=False) if read=='postgres' else mapping
        qualified = qualify(tree, dialect=read, schema=schema_mapping, infer_schema=False,
                            validate_qualify_columns=True, quote_identifiers=True)
    except (sqlglot.errors.SqlglotError, ValueError, TypeError):
        raise reject('字段不存在、含糊或无法可靠校验', 'COLUMN_DENIED') from None
    # qualification expands stars and resolves CTE aliases; physical tables remain from scopes above.
    selected_columns = tuple(sorted({c.sql(dialect=read) for c in qualified.find_all(exp.Column)}))
    if read == 'postgres':
        # psycopg interprets percent signs even inside SQL string literals. Escape literals before inserting binds.
        prefix = '__c_param_' + uuid.uuid4().hex + '_'
        for placeholder in list(qualified.find_all(exp.Placeholder)):
            placeholder.replace(exp.Var(this=prefix + placeholder.name + '__'))
        rendered = qualified.sql(dialect=read).replace('%', '%%')
        for name in parameter_names:
            rendered = rendered.replace(prefix+name+'__', '%('+name+')s')
    else:
        rendered = qualified.sql(dialect=read)
    return ValidatedQuery(rendered, dict(params), tuple(sorted(sources)), selected_columns)
