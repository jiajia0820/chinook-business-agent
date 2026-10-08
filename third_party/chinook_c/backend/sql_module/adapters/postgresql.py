from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def postgres_engine(url, profile):
    return create_engine(url, poolclass=NullPool, hide_parameters=True,
                         connect_args={'connect_timeout': profile.database.get('connect_timeout_seconds', 5)})
