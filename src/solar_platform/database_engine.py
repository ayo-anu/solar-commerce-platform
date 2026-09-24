"""Opt-in PostgreSQL engine construction; no application lifecycle ownership."""

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine


class DatabaseEngineOptionsError(ValueError):
    """Reject invalid engine options without echoing supplied values."""


def _in_range(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def create_postgresql_url(
    *,
    host: str,
    port: int,
    name: str,
    user: str,
    password: str,
) -> URL:
    """Build a structured Psycopg URL without exposing supplied credentials."""
    if not all(
        (
            isinstance(host, str) and bool(host),
            _in_range(port, 1, 65535),
            isinstance(name, str) and bool(name),
            isinstance(user, str) and bool(user),
            isinstance(password, str) and bool(password),
        )
    ):
        raise DatabaseEngineOptionsError("Database engine options are invalid.")
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=port,
        database=name,
    )


def create_database_engine(
    *,
    host: str,
    port: int,
    name: str,
    user: str,
    password: str,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: int,
    connect_timeout_seconds: int,
    statement_timeout_ms: int,
) -> Engine:
    """Build a finite PostgreSQL QueuePool without opening a connection."""
    if not all(
        (
            _in_range(pool_size, 1, 8),
            _in_range(max_overflow, 0, 4),
            _in_range(pool_timeout_seconds, 1, 30),
            _in_range(connect_timeout_seconds, 2, 30),
            _in_range(statement_timeout_ms, 100, 60000),
        )
    ):
        raise DatabaseEngineOptionsError("Database engine options are invalid.")
    if pool_size + max_overflow > 8:
        raise DatabaseEngineOptionsError("Database engine options are invalid.")
    url = create_postgresql_url(
        host=host,
        port=port,
        name=name,
        user=user,
        password=password,
    )
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": connect_timeout_seconds,
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
    )
