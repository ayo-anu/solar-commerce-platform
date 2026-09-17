"""Unit tests for typed process configuration."""

import traceback
from collections.abc import Iterator, Mapping

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from solar_platform.settings import (
    ENVIRONMENT_VARIABLE,
    RuntimeEnvironment,
    SecretSetting,
    Settings,
    SettingsLoadError,
    load_settings,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("development", RuntimeEnvironment.DEVELOPMENT),
        ("test", RuntimeEnvironment.TEST),
        ("production", RuntimeEnvironment.PRODUCTION),
    ),
)
def test_load_settings_accepts_each_runtime_environment(
    value: str, expected: RuntimeEnvironment
) -> None:
    settings = load_settings({ENVIRONMENT_VARIABLE: value})

    assert settings.environment is expected


@pytest.mark.parametrize("value", ("", "Development", "TEST", "staging"))
def test_load_settings_rejects_invalid_runtime_environment(value: str) -> None:
    expected = (
        "Application configuration is invalid: "
        "SOLAR_PLATFORM_ENVIRONMENT has an invalid value."
    )

    with pytest.raises(SettingsLoadError) as captured:
        load_settings({ENVIRONMENT_VARIABLE: value})

    assert str(captured.value) == expected


def test_load_settings_rejects_missing_runtime_environment() -> None:
    expected = (
        "Application configuration is invalid: SOLAR_PLATFORM_ENVIRONMENT is required."
    )

    with pytest.raises(SettingsLoadError) as captured:
        load_settings({})

    assert str(captured.value) == expected


def test_load_settings_reads_process_environment_when_mapping_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "production")

    assert load_settings().environment is RuntimeEnvironment.PRODUCTION


def test_injected_mapping_is_complete_and_does_not_merge_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENVIRONMENT_VARIABLE, "production")

    assert (
        load_settings({ENVIRONMENT_VARIABLE: "test"}).environment
        is RuntimeEnvironment.TEST
    )
    with pytest.raises(SettingsLoadError):
        load_settings({})


def test_settings_reject_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Settings.model_validate(
            {"environment": "development", "unexpected": "not-allowed"}
        )


def test_settings_are_frozen() -> None:
    settings = Settings(environment=RuntimeEnvironment.DEVELOPMENT)

    with pytest.raises(ValidationError, match="frozen_instance"):
        settings.environment = RuntimeEnvironment.PRODUCTION


class SecretFixture(BaseModel):
    """Exercise the reviewed secret-setting representation without a real setting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: SecretSetting


def test_secret_setting_masks_representations_and_serialization() -> None:
    sentinel = "unique-secret-representation-sentinel"
    fixture = SecretFixture(secret=SecretStr(sentinel))
    wrapped = fixture.secret
    dumped = fixture.model_dump(mode="python")
    serialized = fixture.model_dump_json()

    assert sentinel not in repr(fixture)
    assert sentinel not in str(fixture)
    assert repr(wrapped) == "SecretStr('**********')"
    assert str(wrapped) == "**********"
    assert isinstance(dumped["secret"], SecretStr)
    assert dumped["secret"] is wrapped
    assert sentinel not in serialized
    assert '"secret":"**********"' in serialized
    assert wrapped.get_secret_value() == sentinel


def test_rejected_value_is_absent_from_detached_error_surfaces() -> None:
    sentinel = "unique-rejected-configuration-sentinel"

    with pytest.raises(SettingsLoadError) as captured:
        load_settings({ENVIRONMENT_VARIABLE: sentinel})

    error = captured.value
    rendered_traceback = "".join(traceback.format_exception(error))
    assert str(error) == (
        "Application configuration is invalid: "
        "SOLAR_PLATFORM_ENVIRONMENT has an invalid value."
    )
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert all(sentinel not in repr(argument) for argument in error.args)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.__dict__ == {}
    assert sentinel not in rendered_traceback


def test_configuration_failure_emits_no_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SettingsLoadError):
        load_settings({ENVIRONMENT_VARIABLE: "invalid-and-not-emitted"})

    assert capsys.readouterr() == ("", "")


def test_unexpected_validation_location_uses_generic_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedModel(BaseModel):
        unexpected: int

    with pytest.raises(ValidationError) as validation:
        UnexpectedModel.model_validate({})
    unexpected_error = validation.value

    def invalid_model_validate(values: object) -> Settings:
        raise unexpected_error

    monkeypatch.setattr(
        Settings, "model_validate", staticmethod(invalid_model_validate)
    )

    with pytest.raises(SettingsLoadError) as captured:
        load_settings({ENVIRONMENT_VARIABLE: "development"})

    assert str(captured.value) == "Application configuration is invalid."
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


class TrackingMapping(Mapping[str, str]):
    """Mapping that records key access and rejects iteration."""

    def __init__(self) -> None:
        self.accesses: list[tuple[str, str]] = []

    def __contains__(self, key: object) -> bool:
        self.accesses.append(("contains", str(key)))
        return key == ENVIRONMENT_VARIABLE

    def __getitem__(self, key: str) -> str:
        self.accesses.append(("getitem", key))
        if key != ENVIRONMENT_VARIABLE:
            raise KeyError(key)
        return "development"

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("load_settings must not iterate the environment")

    def __len__(self) -> int:
        raise AssertionError("load_settings must not inspect environment size")


def test_loader_reads_only_recognized_key_and_emits_no_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    environ = TrackingMapping()

    settings = load_settings(environ)

    assert settings.environment is RuntimeEnvironment.DEVELOPMENT
    assert environ.accesses == [
        ("contains", ENVIRONMENT_VARIABLE),
        ("getitem", ENVIRONMENT_VARIABLE),
    ]
    assert capsys.readouterr() == ("", "")
