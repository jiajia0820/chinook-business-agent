from contextlib import contextmanager
import time
from .sqlite import sqlite_engine
from .postgresql import postgres_engine
from ..errors import ModuleError, unavailable, database_error


@contextmanager
def connection(profile, timeout_ms=None):
    engine = None
    try:
        url = profile.connection_url()
        engine = sqlite_engine(url, profile) if profile.database['dialect'] == 'sqlite' else postgres_engine(url, profile)
        with engine.connect() as conn:
            with conn.begin():
                budget = timeout_ms or profile.database.get('metadata_timeout_ms', 5000)
                if profile.database['dialect'] == 'postgresql':
                    conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                    conn.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
                    conn.exec_driver_sql('SET LOCAL statement_timeout = ' + str(int(budget)))
                    conn.exec_driver_sql('SET LOCAL lock_timeout = ' + str(int(budget)))
                else:
                    raw = conn.connection.driver_connection
                    deadline = time.monotonic() + budget / 1000
                    raw.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
                yield conn
    except ModuleError:
        raise
    except ImportError:
        raise unavailable('DEPENDENCY_MISSING', '缺少数据库驱动；请安装 requirements-c.txt') from None
    except Exception as exc:
        raise database_error(exc) from None
    finally:
        if engine is not None:
            engine.dispose()
