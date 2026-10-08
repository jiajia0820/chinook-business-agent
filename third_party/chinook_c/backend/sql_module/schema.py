import hashlib
import json
from sqlalchemy import inspect
from .adapters import connection
from .errors import unavailable


def reflect(profile, allowed, inspector, version):
    db = profile.database
    schemas = set(inspector.get_schema_names())
    for schema in db['allowed_schemas']:
        if schema not in schemas:
            raise unavailable('SCHEMA_MISMATCH', '配置 Schema 不存在或不可读取')
        names = set(inspector.get_table_names(schema=schema))
        declared = {t.split('.')[1] for t in db['allowed_tables'] if t.split('.')[0] == schema}
        if not declared <= names:
            raise unavailable('SCHEMA_MISMATCH', '配置表不存在或不可读取；请核对精确大小写')
    tables, edges, limitations = [], [], []
    for table_id in sorted(allowed):
        schema, name = table_id.split('.')
        cols = inspector.get_columns(name, schema=schema)
        pk = list(inspector.get_pk_constraint(name, schema=schema).get('constrained_columns') or [])
        description = None
        try:
            description = inspector.get_table_comment(name, schema=schema).get('text')
        except NotImplementedError:
            limitations.append('数据库未提供表注释反射')
        unique = inspector.get_unique_constraints(name, schema=schema)
        tables.append(dict(table_id=table_id, schema=schema, name=name, description=description,
                           columns=[dict(name=c['name'], data_type=str(c['type']), nullable=bool(c['nullable']),
                                         description=c.get('comment')) for c in cols],
                           primary_key=pk, unique_constraints=sorted(
                               [dict(name=u.get('name'), columns=list(u['column_names'])) for u in unique],
                               key=lambda u: (u['name'] or '', u['columns']))))
        options = {'postgresql_ignore_search_path': True} if db['dialect'] == 'postgresql' else {}
        for fk in inspector.get_foreign_keys(name, schema=schema, **options):
            target = (fk.get('referred_schema') or schema) + '.' + fk['referred_table']
            if target not in allowed:
                limitations.append('部分外键因目标不在授权范围内而省略')
                continue
            edges.append(dict(name=fk.get('name'), from_table=table_id,
                              from_columns=list(fk['constrained_columns']), to_table=target,
                              to_columns=list(fk['referred_columns'])))
    by_id = {t['table_id']: t for t in tables}
    for table in tables:
        colnames = {c['name'] for c in table['columns']}
        if (len(colnames) != len(table['columns']) or not set(table['primary_key']) <= colnames
                or len(table['primary_key']) != len(set(table['primary_key']))
                or any(not set(u['columns']) <= colnames for u in table['unique_constraints'])):
            raise unavailable('METADATA_INVALID', '数据库字段或主键结构不一致')
    for edge in edges:
        if edge['to_columns'] and all(c is None for c in edge['to_columns']):
            edge['to_columns'] = by_id[edge['to_table']]['primary_key'][:]
        if (not edge['from_columns'] or len(edge['from_columns']) != len(edge['to_columns'])
                or not set(edge['from_columns']) <= {c['name'] for c in by_id[edge['from_table']]['columns']}
                or not set(edge['to_columns']) <= {c['name'] for c in by_id[edge['to_table']]['columns']}):
            raise unavailable('METADATA_INVALID', '数据库外键结构不一致')
    edges.sort(key=lambda e: (e['from_table'], e['name'] or '', e['from_columns'], e['to_table'], e['to_columns']))
    snapshot = dict(profile_id=profile.id, config_version=profile.data['config_version'],
                    database=dict(dialect=db['dialect'], display_name=db['display_name'], server_version=version),
                    tables=tables, foreign_keys=edges, limitations=sorted(set(limitations)))
    fingerprint = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    snapshot['schema_version'] = hashlib.sha256(fingerprint.encode()).hexdigest()
    return snapshot


class SchemaService:
    def __init__(self, registry):
        self.registry = registry

    def get_schema(self, profile_id, access):
        profile, allowed = self.registry.resolve(profile_id, access)
        with connection(profile) as conn:
            version = conn.exec_driver_sql('SELECT sqlite_version()' if profile.database['dialect'] == 'sqlite'
                                          else 'SHOW server_version').scalar_one()
            return reflect(profile, allowed, inspect(conn), str(version))

    def check(self, profile_id, access):
        snapshot = self.get_schema(profile_id, access)
        return dict(profile_id=profile_id, status='success', database=snapshot['database'],
                    table_count=len(snapshot['tables']), schema_version=snapshot['schema_version'])
