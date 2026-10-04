"""Optional server-owned business catalog. No automatic metric or entity retrieval yet."""
from .profiles import read_json, obj, string, strings
from .errors import invalid, unavailable


def load_catalog(profile, snapshot):
    tables = {t['table_id']: {c['name'] for c in t['columns']} for t in snapshot['tables']}
    files = profile.data.get('catalog_files', {})
    result = dict(version='none', metrics=[], dictionary=[], aliases=[])
    if 'metrics' in files:
        data = read_json(profile.directory / files['metrics'])
        obj(data, ['version','metrics'], ['version','metrics'], 'metrics catalog')
        string(data['version'], 'metrics version')
        if not isinstance(data['metrics'],list):
            raise invalid('metrics 必须为数组')
        ids = set()
        for metric in data['metrics']:
            obj(metric, ['metric_id','name','definition','unit','grain','required_columns','aggregation','required_slots','source_refs'],
                ['metric_id','name','definition','required_columns','required_slots','source_refs'], 'metric')
            for key in ('metric_id','name','definition'):
                string(metric[key],key)
            if metric['metric_id'] in ids:
                raise invalid('metric_id 重复')
            ids.add(metric['metric_id'])
            for key in ('unit','grain','aggregation'):
                if key in metric and metric[key] is not None:
                    string(metric[key],key)
            for key in ('required_columns','required_slots','source_refs'):
                strings(metric[key],key,nonempty=False)
            for ref in metric['required_columns']:
                parts=ref.split('.')
                if len(parts)!=3 or '.'.join(parts[:2]) not in tables or parts[2] not in tables['.'.join(parts[:2])]:
                    raise unavailable('CATALOG_MISMATCH','指标引用字段不存在或不在授权范围')
            if not set(metric['required_slots']) <= set(profile.data.get('slots',{})):
                raise invalid('指标引用未声明槽位')
        result.update(version=data['version'], metrics=data['metrics'])
    if 'dictionary' in files:
        data=read_json(profile.directory / files['dictionary'])
        obj(data,['tables'],['tables'],'dictionary')
        if not isinstance(data['tables'],list):
            raise invalid('dictionary.tables 必须为数组')
        seen=set()
        for table in data['tables']:
            obj(table,['table_id','description','columns'],['table_id','columns'],'dictionary table')
            string(table['table_id'],'dictionary.table_id')
            if table['table_id'] not in tables or table['table_id'] in seen:
                raise unavailable('CATALOG_MISMATCH','字段字典表不存在、重复或未授权')
            seen.add(table['table_id'])
            if 'description' in table: string(table['description'],'description')
            if not isinstance(table['columns'],list): raise invalid('字典字段必须为数组')
            seen_columns=set()
            for column in table['columns']:
                obj(column,['name','description','aliases'],['name'],'dictionary column')
                string(column['name'],'column.name')
                if column['name'] not in tables[table['table_id']] or column['name'] in seen_columns:
                    raise unavailable('CATALOG_MISMATCH','字典字段不存在、重复或未授权')
                seen_columns.add(column['name'])
                if 'description' in column: string(column['description'],'description')
                if 'aliases' in column: strings(column['aliases'],'aliases',nonempty=False)
        result['dictionary']=data['tables']
    # Alias value verification/retrieval belongs to stage 4; do not silently enable unvalidated aliases.
    if 'aliases' in files:
        result['alias_configured']=True
    return result
