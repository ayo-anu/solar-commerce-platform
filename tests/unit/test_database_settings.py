"""Unit tests for opt-in, secret-safe database settings."""

import traceback
from pathlib import Path

import pytest

from solar_platform.settings import (
    DatabaseSettingsLoadError,
    load_database_settings,
)

pytestmark = pytest.mark.unit


def _environment(password_file: Path) -> dict[str, str]:
    return {
        "SOLAR_PLATFORM_DB_HOST": "127.0.0.1",
        "SOLAR_PLATFORM_DB_PORT": "5433",
        "SOLAR_PLATFORM_DB_NAME": "solar_platform_test",
        "SOLAR_PLATFORM_DB_USER": "postgres",
        "SOLAR_PLATFORM_DB_PASSWORD_FILE": str(password_file),
    }


@pytest.mark.parametrize("terminators", ("\n", "\r\n", "\n\n", ""))
def test_password_file_removes_only_trailing_line_terminators(
    tmp_path: Path, terminators: str
) -> None:
    sentinel = "  generated-style password with spaces  "
    password_file = tmp_path / "password"
    password_file.write_bytes((sentinel + terminators).encode("utf-8"))

    settings = load_database_settings(_environment(password_file))

    assert settings.password.get_secret_value() == sentinel
    assert sentinel not in repr(settings)
    assert sentinel not in str(settings)
    assert sentinel not in settings.model_dump_json()


def test_password_file_preserves_internal_line_terminators_and_whitespace(
    tmp_path: Path,
) -> None:
    password_file = tmp_path / "password"
    password_file.write_bytes(b" leading\ninternal\tspace \n")

    settings = load_database_settings(_environment(password_file))

    assert settings.password.get_secret_value() == " leading\ninternal\tspace "


@pytest.mark.parametrize("contents", (b"", b"\n", b"\r\n\n"))
def test_empty_password_after_normalization_is_rejected(
    tmp_path: Path, contents: bytes
) -> None:
    password_file = tmp_path / "password"
    password_file.write_bytes(contents)

    with pytest.raises(DatabaseSettingsLoadError, match="Database configuration"):
        load_database_settings(_environment(password_file))


def test_secret_path_and_password_are_absent_from_errors(
    tmp_path: Path,
) -> None:
    sentinel = "secret-path-sentinel"
    password_file = tmp_path / sentinel
    password_file.write_bytes(b"\xff")

    with pytest.raises(DatabaseSettingsLoadError) as captured:
        load_database_settings(_environment(password_file))

    error = captured.value
    rendered = "".join(traceback.format_exception(error))
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert sentinel not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("SOLAR_PLATFORM_DB_POOL_SIZE", "0"),
        ("SOLAR_PLATFORM_DB_MAX_OVERFLOW", "-1"),
        ("SOLAR_PLATFORM_DB_POOL_TIMEOUT_SECONDS", "0"),
        ("SOLAR_PLATFORM_DB_CONNECT_TIMEOUT_SECONDS", "1"),
        ("SOLAR_PLATFORM_DB_STATEMENT_TIMEOUT_MS", "0"),
        ("SOLAR_PLATFORM_DB_PORT", "65536"),
        ("SOLAR_PLATFORM_DB_POOL_SIZE", "not-a-number"),
    ),
)
def test_invalid_database_limits_are_rejected(
    tmp_path: Path, name: str, value: str
) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("secret\n")
    environ = _environment(password_file)
    environ[name] = value

    with pytest.raises(DatabaseSettingsLoadError, match="Database configuration"):
        load_database_settings(environ)


def test_combined_pool_capacity_is_bounded(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("secret\n")
    environ = _environment(password_file)
    environ["SOLAR_PLATFORM_DB_POOL_SIZE"] = "8"
    environ["SOLAR_PLATFORM_DB_MAX_OVERFLOW"] = "1"

    with pytest.raises(DatabaseSettingsLoadError):
        load_database_settings(environ)


def test_database_settings_defaults_are_finite(tmp_path: Path) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("secret\n")

    settings = load_database_settings(_environment(password_file))

    assert settings.pool_size == 2
    assert settings.max_overflow == 1
    assert settings.pool_timeout_seconds == 2
    assert settings.connect_timeout_seconds == 5
    assert settings.statement_timeout_ms == 15000


def test_database_loader_does_not_merge_supplied_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password_file = tmp_path / "password"
    password_file.write_text("secret\n")
    monkeypatch.setenv("SOLAR_PLATFORM_DB_HOST", "other-host")
    environ = _environment(password_file)
    environ.pop("SOLAR_PLATFORM_DB_HOST")

    with pytest.raises(DatabaseSettingsLoadError):
        load_database_settings(environ)
