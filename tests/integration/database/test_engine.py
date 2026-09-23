"""Opt-in PostgreSQL checks against the disposable B3.1.1 test service."""

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from solar_platform.database_engine import create_database_engine
from solar_platform.settings import DatabaseSettings

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _test_engine(
    settings: DatabaseSettings,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    pool_timeout_seconds: int | None = None,
    statement_timeout_ms: int | None = None,
) -> Engine:
    return create_database_engine(
        host=settings.host,
        port=settings.port,
        name=settings.name,
        user=settings.user,
        password=settings.password.get_secret_value(),
        pool_size=settings.pool_size if pool_size is None else pool_size,
        max_overflow=(settings.max_overflow if max_overflow is None else max_overflow),
        pool_timeout_seconds=(
            settings.pool_timeout_seconds
            if pool_timeout_seconds is None
            else pool_timeout_seconds
        ),
        connect_timeout_seconds=settings.connect_timeout_seconds,
        statement_timeout_ms=(
            settings.statement_timeout_ms
            if statement_timeout_ms is None
            else statement_timeout_ms
        ),
    )


@pytest.fixture
def engine(test_database_settings: DatabaseSettings) -> Iterator[Engine]:
    database_engine = _test_engine(test_database_settings)
    try:
        yield database_engine
    finally:
        database_engine.dispose()


def test_connects_to_expected_database_and_uses_statement_timeout(
    engine: Engine, test_database_settings: DatabaseSettings
) -> None:
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_database()")) == (
            "solar_platform_test"
        )
        assert connection.scalar(text("SELECT 1")) == 1
        assert (
            connection.scalar(
                text(
                    "SELECT setting::integer FROM pg_settings "
                    "WHERE name = 'statement_timeout'"
                )
            )
            == test_database_settings.statement_timeout_ms
        )


def test_pool_checkout_wait_is_bounded(
    test_database_settings: DatabaseSettings,
) -> None:
    engine = _test_engine(
        test_database_settings,
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=1,
    )
    try:
        with engine.connect():
            with pytest.raises(PoolTimeoutError):
                engine.connect()
    finally:
        engine.dispose()


def test_statement_timeout_cancels_slow_statement(
    test_database_settings: DatabaseSettings,
) -> None:
    engine = _test_engine(test_database_settings, statement_timeout_ms=500)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT setting::integer FROM pg_settings "
                        "WHERE name = 'statement_timeout'"
                    )
                )
                == 500
            )
            with pytest.raises(DBAPIError):
                connection.execute(text("SELECT pg_sleep(2)"))
    finally:
        engine.dispose()
