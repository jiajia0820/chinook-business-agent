from pathlib import Path
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def sqlite_engine(url, profile):
    def creator():
        conn = sqlite3.connect(Path(url.database).as_uri() + '?mode=ro', uri=True,
                               timeout=profile.database.get('connect_timeout_seconds', 5))
        conn.execute('PRAGMA query_only=ON')
        return conn
    return create_engine('sqlite://', creator=creator, poolclass=NullPool)
