"""Alembic lifecycle tests against fixture-owned PostgreSQL databases."""

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

from solar_platform.database_engine import create_postgresql_url
from solar_platform.settings import DatabaseSettings

pytestmark = [pytest.mark.integration, pytest.mark.migration]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
TEST_PASSWORD_FILE = PROJECT_ROOT / ".secrets" / "postgres-test-password"
_DATABASE_NAME_PATTERN = re.compile(r"\Asolar_platform_migration_[0-9a-f]{32}\Z")
_FORBIDDEN_DATABASE_NAMES = frozenset(
    {"postgres", "template0", "template1", "solar_platform_test"}
)
_SECRET_DISCLOSURE_ERROR = (
    "Alembic command output contained prohibited database secret material; "
    "diagnostics suppressed."
)
_SCHEMAS_QUERY = text(
    "SELECT namespace.nspname "
    "FROM pg_namespace AS namespace "
    "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND namespace.nspname NOT LIKE 'pg_toast%' "
    "AND namespace.nspname NOT LIKE 'pg_temp_%' "
    "ORDER BY namespace.nspname"
)
_RELATIONS_QUERY = text(
    "SELECT namespace.nspname, relation.relname, relation.relkind "
    "FROM pg_class AS relation "
    "JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace "
    "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND namespace.nspname NOT LIKE 'pg_toast%' "
    "AND namespace.nspname NOT LIKE 'pg_temp_%' "
    "ORDER BY namespace.nspname, relation.relname, relation.relkind"
)


@dataclass(frozen=True)
class _OwnedDatabaseName:
    value: str


@dataclass(frozen=True)
class _CatalogSnapshot:
    schemas: tuple[str, ...]
    relations: tuple[tuple[str, str, str], ...]
    version_table: str | None
    revisions: frozenset[str]


def _validate_owned_database_name(
    candidate: str, *, owned_name: _OwnedDatabaseName
) -> None:
    if candidate in _FORBIDDEN_DATABASE_NAMES:
        raise RuntimeError("refusing to operate on a forbidden database name")
    if _DATABASE_NAME_PATTERN.fullmatch(candidate) is None:
        raise RuntimeError("migration database name does not match the required shape")
    if candidate != owned_name.value:
        raise RuntimeError("migration database name is not owned by this fixture")


def _quoted_owned_database_name(
    connection: Connection, *, candidate: str, owned_name: _OwnedDatabaseName
) -> str:
    _validate_owned_database_name(candidate, owned_name=owned_name)
    return connection.dialect.identifier_preparer.quote(candidate)


def _database_engine(settings: DatabaseSettings) -> Engine:
    return create_engine(
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


@pytest.fixture
def migration_database_settings(
    test_database_settings: DatabaseSettings,
) -> Iterator[DatabaseSettings]:
    """Create and destroy one internally named database on the validated test server."""
    owned_name = _OwnedDatabaseName(value=f"solar_platform_migration_{uuid4().hex}")
    created = False
    admin_engine = _database_engine(test_database_settings).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            quoted_name = _quoted_owned_database_name(
                connection,
                candidate=owned_name.value,
                owned_name=owned_name,
            )
            connection.exec_driver_sql(f"CREATE DATABASE {quoted_name}")
            created = True

        yield test_database_settings.model_copy(update={"name": owned_name.value})
    finally:
        try:
            if created:
                with admin_engine.connect() as connection:
                    quoted_name = _quoted_owned_database_name(
                        connection,
                        candidate=owned_name.value,
                        owned_name=owned_name,
                    )
                    connection.exec_driver_sql(
                        f"DROP DATABASE {quoted_name} WITH (FORCE)"
                    )
        finally:
            admin_engine.dispose()


def _catalog_snapshot(settings: DatabaseSettings) -> _CatalogSnapshot:
    engine = _database_engine(settings)
    try:
        with engine.connect() as connection:
            schemas = tuple(str(row[0]) for row in connection.execute(_SCHEMAS_QUERY))
            relations = tuple(
                (str(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(_RELATIONS_QUERY)
            )
            version_table = connection.scalar(
                text("SELECT to_regclass('public.alembic_version')::text")
            )
            revisions = (
                frozenset(
                    str(row[0])
                    for row in connection.execute(
                        text("SELECT version_num FROM public.alembic_version")
                    )
                )
                if version_table is not None
                else frozenset()
            )
    finally:
        engine.dispose()
    return _CatalogSnapshot(
        schemas=schemas,
        relations=relations,
        version_table=str(version_table) if version_table is not None else None,
        revisions=revisions,
    )


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(toml_file=str(PYPROJECT)))


def _repository_head_state(scripts: ScriptDirectory) -> frozenset[str]:
    heads = frozenset(scripts.get_heads())
    if len(heads) > 1:
        pytest.fail("repository migration policy permits at most one Alembic head")
    return heads


def _direct_predecessor_state(head: Script) -> frozenset[str]:
    down_revision = head.down_revision
    if down_revision is None:
        return frozenset()
    if isinstance(down_revision, str):
        return frozenset({down_revision})
    return frozenset(down_revision)


def _alembic_environment(settings: DatabaseSettings) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("SOLAR_PLATFORM_DB_")
    }
    environment.update(
        {
            "SOLAR_PLATFORM_DB_HOST": settings.host,
            "SOLAR_PLATFORM_DB_PORT": str(settings.port),
            "SOLAR_PLATFORM_DB_NAME": settings.name,
            "SOLAR_PLATFORM_DB_USER": settings.user,
            "SOLAR_PLATFORM_DB_PASSWORD_FILE": str(TEST_PASSWORD_FILE),
            "SOLAR_PLATFORM_DB_POOL_SIZE": str(settings.pool_size),
            "SOLAR_PLATFORM_DB_MAX_OVERFLOW": str(settings.max_overflow),
            "SOLAR_PLATFORM_DB_POOL_TIMEOUT_SECONDS": str(
                settings.pool_timeout_seconds
            ),
            "SOLAR_PLATFORM_DB_CONNECT_TIMEOUT_SECONDS": str(
                settings.connect_timeout_seconds
            ),
            "SOLAR_PLATFORM_DB_STATEMENT_TIMEOUT_MS": str(
                settings.statement_timeout_ms
            ),
        }
    )
    return environment


def _redact_diagnostics(output: str, secret_representations: frozenset[str]) -> str:
    sanitized = output
    for secret in sorted(secret_representations, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def _run_alembic(
    settings: DatabaseSettings, working_directory: Path, *arguments: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "alembic",
            "-c",
            str(PYPROJECT),
            *arguments,
        ],
        cwd=working_directory,
        env=_alembic_environment(settings),
        text=True,
        capture_output=True,
        check=False,
    )

    password = settings.password.get_secret_value()
    secret_representations = frozenset(
        {password, quote(password, safe=""), quote_plus(password, safe="")}
    )
    sanitized_stdout = _redact_diagnostics(result.stdout, secret_representations)
    sanitized_stderr = _redact_diagnostics(result.stderr, secret_representations)
    secret_detected = any(
        secret in output
        for secret in secret_representations
        for output in (result.stdout, result.stderr)
    )
    if secret_detected:
        pytest.fail(_SECRET_DISCLOSURE_ERROR, pytrace=False)
    if result.returncode != 0:
        pytest.fail(
            "Alembic command failed "
            f"with exit code {result.returncode}.\n"
            f"stdout:\n{sanitized_stdout}\n"
            f"stderr:\n{sanitized_stderr}",
            pytrace=False,
        )


def test_database_name_guard_accepts_exact_fixture_owned_name() -> None:
    owned_name = _OwnedDatabaseName(
        value="solar_platform_migration_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    _validate_owned_database_name(owned_name.value, owned_name=owned_name)


@pytest.mark.parametrize(
    ("candidate", "error"),
    (
        ("solar_platform_test", "forbidden database name"),
        ("not_a_migration_database", "required shape"),
        (
            "solar_platform_migration_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "not owned by this fixture",
        ),
    ),
)
def test_database_name_guard_rejects_unsafe_candidates(
    candidate: str, error: str
) -> None:
    owned_name = _OwnedDatabaseName(
        value="solar_platform_migration_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )

    with pytest.raises(RuntimeError, match=error):
        _validate_owned_database_name(candidate, owned_name=owned_name)


@pytest.mark.parametrize("secret_stream", ("stdout", "stderr"))
def test_alembic_secret_disclosure_suppresses_all_diagnostics(
    secret_stream: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password = "migration-secret-sentinel"
    settings = DatabaseSettings(
        host="127.0.0.1",
        port=5433,
        name="solar_platform_migration_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        user="postgres",
        password=SecretStr(password),
    )
    stdout = (
        f"unsafe stdout containing {password}"
        if secret_stream == "stdout"
        else "safe stdout"
    )
    stderr = (
        f"unsafe stderr containing {password}"
        if secret_stream == "stderr"
        else "safe stderr"
    )

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout=stdout, stderr=stderr
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(pytest.fail.Exception) as failure:
        _run_alembic(settings, tmp_path, "current")

    failure_text = str(failure.value)
    assert failure_text == _SECRET_DISCLOSURE_ERROR
    assert password not in failure_text
    assert "unsafe stdout" not in failure_text
    assert "unsafe stderr" not in failure_text


def test_alembic_non_secret_failure_retains_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = DatabaseSettings(
        host="127.0.0.1",
        port=5433,
        name="solar_platform_migration_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        user="postgres",
        password=SecretStr("migration-secret-sentinel"),
    )

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=7,
            stdout="safe stdout diagnostic",
            stderr="safe stderr diagnostic",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(pytest.fail.Exception) as failure:
        _run_alembic(settings, tmp_path, "current")

    failure_text = str(failure.value)
    assert "exit code 7" in failure_text
    assert "safe stdout diagnostic" in failure_text
    assert "safe stderr diagnostic" in failure_text
    assert _SECRET_DISCLOSURE_ERROR not in failure_text


@pytest.mark.postgres
def test_current_leaves_fresh_database_catalog_unchanged(
    migration_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    before = _catalog_snapshot(migration_database_settings)
    assert before.relations == ()
    assert before.version_table is None
    assert before.revisions == frozenset()

    _run_alembic(migration_database_settings, tmp_path, "current")

    assert _catalog_snapshot(migration_database_settings) == before


@pytest.mark.postgres
def test_fresh_database_upgrades_to_head_idempotently_and_passes_check(
    migration_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    heads = _repository_head_state(_script_directory())

    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")

    at_head = _catalog_snapshot(migration_database_settings)
    assert at_head.version_table is not None
    assert at_head.revisions == heads

    _run_alembic(migration_database_settings, tmp_path, "check")
    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")

    assert _catalog_snapshot(migration_database_settings) == at_head


@pytest.mark.postgres
def test_direct_predecessor_revision_set_upgrades_to_head(
    migration_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    scripts = _script_directory()
    heads = _repository_head_state(scripts)
    if not heads:
        pytest.skip("current-to-head requires at least one real migration revision")

    head = scripts.get_revision(next(iter(heads)))
    assert head is not None
    predecessors = _direct_predecessor_state(head)
    if predecessors:
        for predecessor in sorted(predecessors):
            _run_alembic(migration_database_settings, tmp_path, "upgrade", predecessor)
    else:
        _run_alembic(migration_database_settings, tmp_path, "upgrade", "base")
    assert _catalog_snapshot(migration_database_settings).revisions == predecessors

    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")

    assert _catalog_snapshot(migration_database_settings).revisions == heads


@pytest.mark.postgres
def test_one_step_downgrade_reaches_complete_predecessor_set_then_reupgrades(
    migration_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    scripts = _script_directory()
    heads = _repository_head_state(scripts)
    if not heads:
        pytest.skip("one-step downgrade requires at least one real migration revision")

    head = scripts.get_revision(next(iter(heads)))
    assert head is not None
    predecessors = _direct_predecessor_state(head)
    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")
    assert _catalog_snapshot(migration_database_settings).revisions == heads

    _run_alembic(migration_database_settings, tmp_path, "downgrade", "-1")

    assert _catalog_snapshot(migration_database_settings).revisions == predecessors
    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")
    assert _catalog_snapshot(migration_database_settings).revisions == heads


@pytest.mark.postgres
def test_head_downgrades_to_managed_base_catalog(
    migration_database_settings: DatabaseSettings, tmp_path: Path
) -> None:
    heads = _repository_head_state(_script_directory())
    _run_alembic(migration_database_settings, tmp_path, "upgrade", "base")
    managed_base = _catalog_snapshot(migration_database_settings)
    assert managed_base.version_table is not None
    assert managed_base.revisions == frozenset()

    _run_alembic(migration_database_settings, tmp_path, "upgrade", "head")
    assert _catalog_snapshot(migration_database_settings).revisions == heads
    _run_alembic(migration_database_settings, tmp_path, "downgrade", "base")

    assert _catalog_snapshot(migration_database_settings) == managed_base
