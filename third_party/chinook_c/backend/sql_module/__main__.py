import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from .profiles import Registry, AccessContext, DEFAULT_PROFILES
from .profiles import read_json
from .schema import SchemaService
from .errors import ModuleError, invalid


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='C 模块终端验收')
    parser.add_argument('--profiles-dir', default=str(DEFAULT_PROFILES))
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('profiles')
    for command in ('check', 'tables', 'table', 'foreign-keys', 'export-schema'):
        sub = subs.add_parser(command)
        sub.add_argument('profile_id')
        if command == 'table':
            sub.add_argument('table_id')
        if command == 'export-schema':
            sub.add_argument('--output', required=True)
    ask = subs.add_parser('ask', help='C 内部基础 NL 查询草案，非 B HTTP 接口')
    ask.add_argument('profile_id')
    ask.add_argument('question', nargs='?')
    ask.add_argument('--request-file')
    ask.add_argument('--demo-model', action='store_true', help='固定离线演示，不代表真实模型')
    query = subs.add_parser('query', help='v0.3 SQL 工具本地调用')
    query.add_argument('--request-file', required=True)
    args = parser.parse_args()
    try:
        registry = Registry.load(args.profiles_dir)
        access = AccessContext.local(registry)
        service = SchemaService(registry)
        if args.command == 'profiles':
            result = [dict(profile_id=p.id, mode=p.data['mode'], config_version=p.data['config_version'],
                           display_name=p.database.get('display_name')) for p in registry.profiles.values()]
        elif args.command == 'ask':
            import uuid
            from .generation import DemoJSONModel
            from .service import NLQueryService
            if args.request_file:
                if args.question:
                    raise invalid('question 与 --request-file 不能同时提供')
                request = read_json(args.request_file)
                if not isinstance(request, dict) or request.get('profile_id') != args.profile_id:
                    raise invalid('请求 profile_id 必须与命令 profile_id 一致')
            else:
                request = dict(request_id='cli-'+str(uuid.uuid4()),profile_id=args.profile_id,question=args.question)
            result = NLQueryService(registry, DemoJSONModel() if args.demo_model else None).answer_sql(request, access)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status']=='answered' else 1
        elif args.command == 'query':
            from .execution import SQLExecutor
            result = SQLExecutor(registry).execute_sql(read_json(args.request_file),access)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status']=='success' else 1
        elif args.command == 'check':
            result = service.check(args.profile_id, access)
        else:
            snapshot = service.get_schema(args.profile_id, access)
            if args.command == 'tables':
                result = [t['table_id'] for t in snapshot['tables']]
            elif args.command == 'table':
                matches = [t for t in snapshot['tables'] if t['table_id'] == args.table_id]
                if not matches:
                    raise invalid('表不存在、未授权或未使用 schema.table')
                result = dict(table=matches[0], foreign_keys=[f for f in snapshot['foreign_keys'] if f['from_table'] == args.table_id])
            elif args.command == 'foreign-keys':
                result = snapshot['foreign_keys']
            else:
                atomic_json(args.output, snapshot)
                result = dict(status='success', profile_id=args.profile_id, schema_version=snapshot['schema_version'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ModuleError as exc:
        print(json.dumps({'status': 'error', 'error': exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1
    except OSError:
        print(json.dumps({'status': 'error', 'error': invalid('无法写入导出文件').as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
