"""Opt-in real PostgreSQL checks for B3.1.4 resource lifecycle."""

from collections.abc import Iterator
from typing import cast

import pytest
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

from solar_platform.database_engine import create_database_engine
from solar_platform.database_runtime import DatabaseRuntime, DatabaseRuntimeClosedError
from solar_platform.settings import DatabaseSettings

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
def runtime_and_pool(
    test_database_settings: DatabaseSettings,
) -> Iterator[tuple[DatabaseRuntime, QueuePool]]:
    settings = test_database_settings
    engine = create_database_engine(
        host=settings.host,
        port=settings.port,
        name=settings.name,
        user=settings.user,
        password=settings.password.get_secret_value(),
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout_seconds=settings.pool_timeout_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    runtime = DatabaseRuntime(engine)
    try:
        yield runtime, cast(QueuePool, engine.pool)
    finally:
        runtime.shutdown()


def test_readiness_releases_its_checked_out_connection(
    runtime_and_pool: tuple[DatabaseRuntime, QueuePool],
) -> None:
    runtime, pool = runtime_and_pool

    assert pool.checkedout() == 0
    assert runtime.is_ready() is True
    assert pool.checkedout() == 0


def test_read_only_operation_rolls_back_closes_and_returns_connection(
    runtime_and_pool: tuple[DatabaseRuntime, QueuePool],
) -> None:
    runtime, pool = runtime_and_pool

    with runtime.operation() as operation:
        assert operation.session.scalar(text("SELECT 1")) == 1
        assert pool.checkedout() == 1

    assert pool.checkedout() == 0


def test_authorized_commit_closes_and_returns_connection(
    runtime_and_pool: tuple[DatabaseRuntime, QueuePool],
) -> None:
    runtime, pool = runtime_and_pool

    with runtime.operation() as operation:
        assert operation.session.scalar(text("SELECT txid_current()")) is not None
        operation.authorize_commit()

    assert pool.checkedout() == 0


def test_failed_operation_rolls_back_closes_and_returns_connection(
    runtime_and_pool: tuple[DatabaseRuntime, QueuePool],
) -> None:
    runtime, pool = runtime_and_pool

    with pytest.raises(ValueError, match="application failure"):
        with runtime.operation() as operation:
            assert operation.session.scalar(text("SELECT 1")) == 1
            raise ValueError("application failure")

    assert pool.checkedout() == 0


def test_normal_shutdown_is_terminal_for_readiness_and_operations(
    runtime_and_pool: tuple[DatabaseRuntime, QueuePool],
) -> None:
    runtime, pool = runtime_and_pool
    assert runtime.is_ready() is True
    assert pool.checkedout() == 0

    runtime.shutdown()

    assert runtime.is_ready() is False
    with pytest.raises(DatabaseRuntimeClosedError):
        with runtime.operation():
            pass
    assert pool.checkedout() == 0
