"""Typed process configuration for the application composition boundary."""

import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

ENVIRONMENT_VARIABLE = "SOLAR_PLATFORM_ENVIRONMENT"
SecretSetting = Annotated[SecretStr, Field(repr=False)]

_FIELD_ENVIRONMENT_VARIABLES = {"environment": ENVIRONMENT_VARIABLE}
_GENERIC_CONFIGURATION_ERROR = "Application configuration is invalid."


class SettingsLoadError(RuntimeError):
    """Report invalid process configuration without exposing submitted values."""


class RuntimeEnvironment(StrEnum):
    """Supported application runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseModel):
    """Validated, immutable process configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: RuntimeEnvironment


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
