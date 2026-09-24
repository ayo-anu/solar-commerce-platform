"""Database-free checks for the Alembic script environment."""

import runpy
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
import sqlalchemy
from alembic import command, context
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import NullPool

import solar_platform.settings as settings_module
from solar_platform.database_metadata import database_metadata
from solar_platform.settings import DatabaseSettings

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
MIGRATIONS = PROJECT_ROOT / "migrations"


def _config() -> Config:
    return Config(toml_file=str(PYPROJECT))


def test_pyproject_only_configuration_resolves_from_another_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _config()
    scripts = ScriptDirectory.from_config(config)

    assert Path(scripts.dir) == MIGRATIONS
    assert scripts.get_heads() == []
    assert config.get_main_option("sqlalchemy.url") is None
    assert (MIGRATIONS / "script.py.mako").is_file()
    assert (MIGRATIONS / "versions").is_dir()
    assert not list((MIGRATIONS / "versions").glob("*.py"))


def test_heads_and_history_do_not_run_the_database_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SOLAR_PLATFORM_DB_HOST", raising=False)
    monkeypatch.delenv("SOLAR_PLATFORM_DB_PASSWORD_FILE", raising=False)

    command.heads(_config())
    command.history(_config())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_online_environment_uses_only_the_migration_engine_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = DatabaseSettings(
        host="127.0.0.1",
        port=5433,
        name="solar_platform_test",
        user="postgres",
        password=SecretStr("secret-sentinel"),
        pool_size=8,
        max_overflow=0,
        pool_timeout_seconds=30,
        connect_timeout_seconds=7,
        statement_timeout_ms=12345,
    )
    captured: dict[str, object] = {}
    fake_engine = MagicMock()
    connection = object()
    fake_engine.connect.return_value = nullcontext(connection)

    def fake_create_engine(url: URL, **kwargs: object) -> Engine:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return cast(Engine, fake_engine)

    configured: dict[str, object] = {}

    def fake_configure(**kwargs: object) -> None:
        configured.update(kwargs)

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    monkeypatch.setattr(settings_module, "load_database_settings", lambda: settings)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(context, "configure", fake_configure)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", lambda: None)

    runpy.run_path(str(MIGRATIONS / "env.py"))

    url = captured["url"]
    assert isinstance(url, URL)
    assert url.drivername == "postgresql+psycopg"
    assert "secret-sentinel" not in str(url)
    assert captured["kwargs"] == {
        "poolclass": NullPool,
        "connect_args": {"connect_timeout": 7},
    }
    assert configured == {
        "connection": connection,
        "target_metadata": database_metadata,
    }
    fake_engine.dispose.assert_called_once_with()
