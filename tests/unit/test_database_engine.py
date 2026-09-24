"""Unit tests for bounded, lazily connecting PostgreSQL engine construction."""

from typing import cast

import pytest
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import QueuePool

import solar_platform.database_engine as database_engine
from solar_platform.database_engine import (
    DatabaseEngineOptionsError,
    create_database_engine,
    create_postgresql_url,
)

pytestmark = pytest.mark.unit


def _options() -> dict[str, str | int]:
    return {
        "host": "127.0.0.1",
        "port": 5433,
        "name": "solar_platform_test",
        "user": "postgres",
        "password": " secret-sentinel ",
        "pool_size": 2,
        "max_overflow": 1,
        "pool_timeout_seconds": 2,
        "connect_timeout_seconds": 5,
        "statement_timeout_ms": 15000,
    }


def test_url_builder_uses_structured_psycopg_url_without_exposing_password() -> None:
    url = create_postgresql_url(
        host="127.0.0.1",
        port=5433,
        name="solar_platform_test",
        user="postgres",
        password=" secret-sentinel ",
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "127.0.0.1"
    assert url.port == 5433
    assert url.database == "solar_platform_test"
    assert url.username == "postgres"
    assert url.password == " secret-sentinel "
    assert "secret-sentinel" not in str(url)
    assert "secret-sentinel" not in repr(url)


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("host", ""),
        ("port", 0),
        ("name", ""),
        ("user", ""),
        ("password", ""),
    ),
)
def test_url_builder_rejects_invalid_values_without_repeating_them(
    name: str, value: object
) -> None:
    options: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 5433,
        "name": "solar_platform_test",
        "user": "postgres",
        "password": "secret-sentinel",
    }
    options[name] = value

    with pytest.raises(DatabaseEngineOptionsError) as captured:
        create_postgresql_url(**options)  # type: ignore[arg-type]

    assert str(captured.value) == "Database engine options are invalid."
    assert "secret-sentinel" not in repr(captured.value)
    if value:
        assert str(value) not in str(captured.value)


def test_engine_uses_explicit_dialect_pool_and_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = cast(Engine, object())

    def fake_create_engine(url: URL, **kwargs: object) -> Engine:
        captured["url"] = url
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(database_engine, "create_engine", fake_create_engine)

    result = create_database_engine(**_options())  # type: ignore[arg-type]

    assert result is sentinel
    url = captured["url"]
    assert isinstance(url, URL)
    assert url.drivername == "postgresql+psycopg"
    assert url.password == " secret-sentinel "
    assert "secret-sentinel" not in str(url)
    assert captured["pool_size"] == 2
    assert captured["max_overflow"] == 1
    assert captured["pool_timeout"] == 2
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=15000",
    }


def test_engine_construction_creates_an_empty_bounded_pool() -> None:
    engine = create_database_engine(**_options())  # type: ignore[arg-type]
    try:
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.size() == 2
        assert engine.pool.checkedin() == 0
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("pool_size", 0),
        ("max_overflow", -1),
        ("pool_timeout_seconds", 0),
        ("connect_timeout_seconds", 1),
        ("statement_timeout_ms", 0),
        ("statement_timeout_ms", "10; SHOW password"),
    ),
)
def test_builder_rejects_invalid_options_without_repeating_them(
    name: str, value: object
) -> None:
    options = _options()
    options[name] = cast(str | int, value)

    with pytest.raises(DatabaseEngineOptionsError) as captured:
        create_database_engine(**options)  # type: ignore[arg-type]

    assert str(captured.value) == "Database engine options are invalid."
    assert "secret-sentinel" not in repr(captured.value)
    assert str(value) not in str(captured.value)
