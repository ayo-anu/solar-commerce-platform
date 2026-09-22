"""Typed process configuration for the application composition boundary."""

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

ENVIRONMENT_VARIABLE = "SOLAR_PLATFORM_ENVIRONMENT"
SecretSetting = Annotated[SecretStr, Field(repr=False)]

_FIELD_ENVIRONMENT_VARIABLES = {"environment": ENVIRONMENT_VARIABLE}
_GENERIC_CONFIGURATION_ERROR = "Application configuration is invalid."
_GENERIC_DATABASE_CONFIGURATION_ERROR = "Database configuration is invalid."

_DATABASE_ENVIRONMENT_VARIABLES = {
    "host": "SOLAR_PLATFORM_DB_HOST",
    "port": "SOLAR_PLATFORM_DB_PORT",
    "name": "SOLAR_PLATFORM_DB_NAME",
    "user": "SOLAR_PLATFORM_DB_USER",
    "password_file": "SOLAR_PLATFORM_DB_PASSWORD_FILE",
    "pool_size": "SOLAR_PLATFORM_DB_POOL_SIZE",
    "max_overflow": "SOLAR_PLATFORM_DB_MAX_OVERFLOW",
    "pool_timeout_seconds": "SOLAR_PLATFORM_DB_POOL_TIMEOUT_SECONDS",
    "connect_timeout_seconds": "SOLAR_PLATFORM_DB_CONNECT_TIMEOUT_SECONDS",
    "statement_timeout_ms": "SOLAR_PLATFORM_DB_STATEMENT_TIMEOUT_MS",
}


class SettingsLoadError(RuntimeError):
    """Report invalid process configuration without exposing submitted values."""


class DatabaseSettingsLoadError(RuntimeError):
    """Report invalid database configuration without exposing submitted values."""


class RuntimeEnvironment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseModel):
    """Validated, immutable process configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: RuntimeEnvironment


class DatabaseSettings(BaseModel):
    """Validated, opt-in database connection and finite-pool configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    name: str = Field(min_length=1)
    user: str = Field(min_length=1)
    password: SecretSetting
    pool_size: int = Field(default=2, ge=1, le=8)
    max_overflow: int = Field(default=1, ge=0, le=4)
    pool_timeout_seconds: int = Field(default=2, ge=1, le=30)
    connect_timeout_seconds: int = Field(default=5, ge=2, le=30)
    statement_timeout_ms: int = Field(default=15000, ge=100, le=60000)

    @model_validator(mode="after")
    def validate_total_pool_capacity(self) -> "DatabaseSettings":
        """Prevent a bounded-but-excessive per-engine connection ceiling."""
        if self.pool_size + self.max_overflow > 8:
            raise ValueError("database pool capacity exceeds the allowed limit")
        return self


def _safe_load_error(validation_error: ValidationError) -> SettingsLoadError:
    failures: set[tuple[str, str]] = set()
    for detail in validation_error.errors(
        include_input=False,
        include_context=False,
        include_url=False,
    ):
        location = detail["loc"]
        if len(location) != 1 or not isinstance(location[0], str):
            return SettingsLoadError(_GENERIC_CONFIGURATION_ERROR)
        environment_variable = _FIELD_ENVIRONMENT_VARIABLES.get(location[0])
        if environment_variable is None:
            return SettingsLoadError(_GENERIC_CONFIGURATION_ERROR)
        category = "required" if detail["type"] == "missing" else "invalid"
        failures.add((environment_variable, category))

    if not failures:
        return SettingsLoadError(_GENERIC_CONFIGURATION_ERROR)

    descriptions = [
        f"{name} is required"
        if category == "required"
        else f"{name} has an invalid value"
        for name, category in sorted(failures)
    ]
    return SettingsLoadError(
        f"Application configuration is invalid: {'; '.join(descriptions)}."
    )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings from the supplied complete mapping or the process environment."""
    source = os.environ if environ is None else environ
    values: dict[str, str] = {}
    if ENVIRONMENT_VARIABLE in source:
        values["environment"] = source[ENVIRONMENT_VARIABLE]
    try:
        return Settings.model_validate(values)
    except ValidationError as validation_error:
        safe_error = _safe_load_error(validation_error)
    raise safe_error


def _read_database_password(path: str) -> SecretStr:
    """Match POSTGRES_PASSWORD_FILE: remove only trailing line terminators."""
    try:
        contents = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        safe_error = DatabaseSettingsLoadError(_GENERIC_DATABASE_CONFIGURATION_ERROR)
    else:
        password = contents.rstrip("\r\n")
        if password:
            return SecretStr(password)
        safe_error = DatabaseSettingsLoadError(_GENERIC_DATABASE_CONFIGURATION_ERROR)
    raise safe_error


def load_database_settings(
    environ: Mapping[str, str] | None = None,
) -> DatabaseSettings:
    """Load database settings only when explicitly requested by a caller."""
    source = os.environ if environ is None else environ
    values: dict[str, str | SecretStr] = {}
    for field_name, environment_name in _DATABASE_ENVIRONMENT_VARIABLES.items():
        if environment_name not in source:
            continue
        value = source[environment_name]
        if field_name == "password_file":
            values["password"] = _read_database_password(value)
        else:
            values[field_name] = value
    try:
        return DatabaseSettings.model_validate(values)
    except ValidationError:
        safe_error = DatabaseSettingsLoadError(_GENERIC_DATABASE_CONFIGURATION_ERROR)
    raise safe_error
