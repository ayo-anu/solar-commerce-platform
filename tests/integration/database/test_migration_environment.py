"""Non-mutating Alembic smoke against the disposable PostgreSQL service."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from solar_platform.database_engine import create_postgresql_url
from solar_platform.settings import DatabaseSettings

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.migration]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
TEST_PASSWORD_FILE = PROJECT_ROOT / ".secrets" / "postgres-test-password"
_RELATIONS_QUERY = text(
    "SELECT namespace.nspname, relation.relname, relation.relkind "
    "FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND namespace.nspname NOT LIKE 'pg_toast%' "
    "ORDER BY namespace.nspname, relation.relname, relation.relkind"
)


def _catalog_snapshot(
    settings: DatabaseSettings,
) -> tuple[str | None, tuple[tuple[str, str, str], ...]]:
    engine = create_engine(
        create_postgresql_url(
            host=settings.host,
            port=settings.port,
            name=settings.name,
            user=settings.user,
            password=settings.password.get_secret_value(),
        ),
        poolclass=NullPool,
        connect_args={"connect_timeout": settings.connect_timeout_seconds},
    )
    try:
        with engine.connect() as connection:
            version_table = connection.scalar(
                text("SELECT to_regclass('public.alembic_version')::text")
            )
            relations = tuple(
                (str(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(_RELATIONS_QUERY)
            )
    finally:
        engine.dispose()
    return version_table, relations


def test_current_leaves_empty_database_catalog_unchanged(
    test_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    before = _catalog_snapshot(test_database_settings)
    assert before == (None, ())

    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "alembic",
            "-c",
            str(PYPROJECT),
            "current",
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "SOLAR_PLATFORM_DB_PASSWORD_FILE": str(TEST_PASSWORD_FILE),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    password = test_database_settings.password.get_secret_value()
    assert password not in result.stdout
    assert password not in result.stderr

    after = _catalog_snapshot(test_database_settings)
    assert after == before
    assert after == (None, ())
