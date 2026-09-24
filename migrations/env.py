"""Alembic environment with command-local PostgreSQL connection policy."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import NullPool

from solar_platform.database_engine import create_postgresql_url
from solar_platform.database_metadata import database_metadata
from solar_platform.settings import DatabaseSettings, load_database_settings

target_metadata = database_metadata


def _database_url(settings: DatabaseSettings) -> URL:
    return create_postgresql_url(
        host=settings.host,
        port=settings.port,
        name=settings.name,
        user=settings.user,
        password=settings.password.get_secret_value(),
    )


def _migration_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(
        _database_url(settings),
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.connect_timeout_seconds},
    )


def run_migrations_offline() -> None:
    """Render SQL without opening a database connection."""
    settings = load_database_settings()
    context.configure(
        url=_database_url(settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through one command-local NullPool connection."""
    settings = load_database_settings()
    engine = _migration_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
