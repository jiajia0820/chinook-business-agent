from dataclasses import dataclass
import copy
from pathlib import Path
import json
import os
from .errors import invalid, unavailable, ModuleError

DEFAULT_PROFILES = Path(__file__).resolve().parents[2] / 'profiles'


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise invalid('JSON 包含重复键')
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(invalid('JSON 非有限数值')))
    except ModuleError:
        raise
    except (OSError, ValueError):
        raise invalid('无法读取有效 UTF-8 JSON 配置') from None


def obj(value, allowed, required=(), label='配置'):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise invalid(label + '字段缺失、未知或类型错误')


def string(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise invalid(label + '必须为非空无首尾空白字符串')
    return value


def strings(value, label, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise invalid(label + '必须为非空字符串数组')
    for item in value:
        string(item, label)
    if len(value) != len(set(value)):
        raise invalid(label + '不能重复')
    return value


def integer(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise invalid(label + '整数范围错误')


@dataclass(frozen=True)
class Profile:
    data: dict
    directory: Path

    @property
    def id(self):
        return self.data['profile_id']

    @property
    def database(self):
        return self.data.get('database', {})

    def connection_url(self):
        from sqlalchemy.engine import make_url, URL
        db = self.database
        if 'sqlite_path' in db:
            try:
                path = (self.directory / db['sqlite_path']).resolve()
            except (OSError, ValueError):
                raise invalid('SQLite 路径无效') from None
            if not path.is_file():
                raise unavailable('DATABASE_MISSING', 'SQLite 文件不存在；不会自动建库')
            return URL.create('sqlite', database=path.as_posix())
        secret = os.environ.get(db['url_env'])
        if not secret:
            raise unavailable('CONFIG_MISSING', '数据库连接环境变量未配置：' + db['url_env'])
        try:
            url = make_url(secret)
            expected = db['dialect']
            drivers = {'sqlite': {'sqlite'}, 'postgresql': {'postgresql', 'postgresql+psycopg'}}
            if url.drivername not in drivers[expected] or url.query:
                raise ValueError()
            if expected == 'sqlite':
                if not url.database or url.database == ':memory:':
                    raise ValueError()
                path = Path(url.database)
                if not path.is_absolute():
                    path = self.directory / path
                path = path.resolve()
                if not path.is_file():
                    raise unavailable('DATABASE_MISSING', 'SQLite 文件不存在；不会自动建库')
                return URL.create('sqlite', database=path.as_posix())
            if not url.host or not url.database or ',' in url.host:
                raise ValueError()
            return url.set(drivername='postgresql+psycopg')
        except ModuleError:
            raise
        except Exception:
            raise invalid('数据库 URL 与方言不匹配或包含不支持的连接参数') from None


def validate_profile(data, directory):
    obj(data, ['profile_id', 'mode', 'config_version', 'database', 'catalog_files', 'slots', 'policy', 'model_profile'],
        ['profile_id', 'mode', 'config_version'])
    for key in ('profile_id', 'config_version'):
        string(data[key], key)
    if data['mode'] not in ('sql_only', 'hybrid', 'rag_only'):
        raise invalid('mode 无效')
    if data['mode'] != 'rag_only' or 'database' in data:
        db = data.get('database')
        obj(db, ['dialect', 'url_env', 'sqlite_path', 'display_name', 'allowed_schemas', 'allowed_tables',
                 'connect_timeout_seconds', 'metadata_timeout_ms'],
            ['dialect', 'display_name', 'allowed_schemas', 'allowed_tables'], 'database')
        if db['dialect'] not in ('sqlite', 'postgresql'):
            raise invalid('不支持的数据库方言')
        string(db['display_name'], 'display_name')
        for key in ('allowed_schemas', 'allowed_tables'):
            strings(db[key], key)
        if any('.' in s for s in db['allowed_schemas']):
            raise invalid('Schema 名称内部不支持点号')
        for table in db['allowed_tables']:
            parts = table.split('.')
            if len(parts) != 2 or not all(parts) or parts[0] not in db['allowed_schemas']:
                raise invalid('allowed_tables 必须是授权 Schema 下的 schema.table')
        if db['dialect'] == 'sqlite' and db['allowed_schemas'] != ['main']:
            raise invalid('SQLite 当前仅支持 main')
        if ('url_env' in db) == ('sqlite_path' in db):
            raise invalid('数据库必须且只能配置 url_env 或 sqlite_path')
        if 'sqlite_path' in db and db['dialect'] != 'sqlite':
            raise invalid('sqlite_path 仅用于 SQLite')
        string(db.get('url_env', db.get('sqlite_path')), '连接引用')
        integer(db.get('connect_timeout_seconds', 5), 1, 30, 'connect_timeout_seconds')
        integer(db.get('metadata_timeout_ms', 5000), 1, 30000, 'metadata_timeout_ms')
    if 'catalog_files' in data:
        obj(data['catalog_files'], ['metrics', 'aliases', 'dictionary'], label='catalog_files')
        for value in data['catalog_files'].values():
            string(value, 'catalog_files 路径')
    if 'slots' in data:
        if not isinstance(data['slots'], dict):
            raise invalid('slots 必须为对象')
        for key, slot in data['slots'].items():
            string(key, 'slot 名称')
            obj(slot, ['type', 'required_by_default', 'values'], ['type'], 'slot')
            if slot['type'] not in ('integer', 'string', 'boolean', 'number', 'array', 'object'):
                raise invalid('slot 类型无效')
            if 'required_by_default' in slot and type(slot['required_by_default']) is not bool:
                raise invalid('required_by_default 必须是 boolean')
            if 'values' in slot:
                strings(slot['values'], 'slot values')
    if 'policy' in data:
        obj(data['policy'], ['max_rows', 'query_timeout_ms'], label='policy')
        integer(data['policy'].get('max_rows', 200), 1, 200, 'max_rows')
        integer(data['policy'].get('query_timeout_ms', 5000), 1, 30000, 'query_timeout_ms')
    if 'model_profile' in data:
        string(data['model_profile'], 'model_profile')
    return Profile(copy.deepcopy(data), Path(directory).resolve())


@dataclass(frozen=True)
class AccessContext:
    tables_by_profile: dict

    @classmethod
    def local(cls, registry):
        return cls({p.id: frozenset(p.database.get('allowed_tables', [])) for p in registry.profiles.values()})


class Registry:
    def __init__(self, profiles):
        self.profiles = {}
        for profile in profiles:
            if profile.id in self.profiles:
                raise invalid('profile_id 重复')
            self.profiles[profile.id] = profile

    @classmethod
    def load(cls, directory=DEFAULT_PROFILES):
        directory = Path(directory)
        if not directory.is_dir():
            raise invalid('profile 配置目录不存在')
        return cls(validate_profile(read_json(path), path.parent) for path in sorted(directory.glob('*/profile.json')))

    def resolve(self, profile_id, access):
        string(profile_id, 'profile_id')
        if profile_id not in self.profiles:
            raise ModuleError('PROFILE_NOT_FOUND', 'profile 不存在')
        profile = self.profiles[profile_id]
        if profile_id not in access.tables_by_profile:
            raise unavailable('ACCESS_DENIED', '未授权访问该 profile')
        if profile.data['mode'] == 'rag_only':
            raise ModuleError('UNSUPPORTED_CAPABILITY', '该 profile 不提供 SQL 能力')
        tables = set(profile.database['allowed_tables']) & set(access.tables_by_profile[profile_id])
        return profile, tables
