"""Unit tests for the application composition root."""

import importlib
import sys
from typing import Any, cast

import fastapi
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import iter_route_contexts
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import solar_platform
import solar_platform.app as app_module
import solar_platform.settings as settings_module
from solar_platform.api.correlation import correlation_middleware
from solar_platform.api.health import LivenessResponse, get_liveness
from solar_platform.api.problems import (
    http_exception_handler,
    request_validation_exception_handler,
    unexpected_exception_handler,
)
from solar_platform.api.request_logging import bind_request_logging
from solar_platform.settings import RuntimeEnvironment, Settings, SettingsLoadError

pytestmark = pytest.mark.unit


def _development_settings() -> Settings:
    return Settings(environment=RuntimeEnvironment.DEVELOPMENT)


def test_factory_returns_app_without_retaining_complete_settings() -> None:
    settings = _development_settings()

    app = app_module.create_app(settings)

    assert isinstance(app, FastAPI)
    assert not hasattr(app.state, "settings")
    assert settings not in vars(app.state).get("_state", {}).values()


def test_explicit_settings_bypass_environment_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_load() -> Settings:
        raise AssertionError("explicit settings must bypass environment loading")

    monkeypatch.setattr(app_module, "load_settings", unexpected_load)

    assert isinstance(app_module.create_app(_development_settings()), FastAPI)


def test_default_factory_loads_settings_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def tracked_load() -> Settings:
        nonlocal calls
        calls += 1
        return _development_settings()

    monkeypatch.setattr(app_module, "load_settings", tracked_load)

    assert isinstance(app_module.create_app(), FastAPI)
    assert calls == 1


def test_invalid_configuration_prevents_app_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_error = SettingsLoadError("safe configuration failure")

    def invalid_load() -> Settings:
        raise expected_error

    def unexpected_fastapi(*args: object, **kwargs: object) -> FastAPI:
        raise AssertionError("FastAPI must not be constructed after validation fails")

    monkeypatch.setattr(app_module, "load_settings", invalid_load)
    monkeypatch.setattr(app_module, "FastAPI", unexpected_fastapi)

    with pytest.raises(SettingsLoadError) as captured:
        app_module.create_app()

    assert captured.value is expected_error


def test_repeated_factory_calls_return_distinct_apps() -> None:
    settings = _development_settings()

    first = app_module.create_app(settings)
    second = app_module.create_app(settings)

    assert first is not second


def test_liveness_response_is_frozen_and_forbids_extras() -> None:
    response = LivenessResponse(status="ok")

    with pytest.raises(ValidationError, match="frozen_instance"):
        cast(Any, response).status = "changed"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        LivenessResponse.model_validate({"status": "ok", "extra": "not-allowed"})


def test_imports_have_no_configuration_or_application_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOLAR_PLATFORM_ENVIRONMENT", raising=False)
    importlib.reload(solar_platform)

    def unexpected_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("imports must not construct or load runtime state")

    monkeypatch.setattr(fastapi, "FastAPI", unexpected_call)
    monkeypatch.setattr(solar_platform, "settings", settings_module)
    monkeypatch.setattr(solar_platform, "app", app_module)
    monkeypatch.delitem(sys.modules, "solar_platform.app")
    monkeypatch.delitem(sys.modules, "solar_platform.settings")

    importlib.import_module("solar_platform.settings")
    imported_app = importlib.import_module("solar_platform.app")

    assert not hasattr(imported_app, "app")


def test_factory_registers_only_reviewed_http_edge_behavior() -> None:
    app = app_module.create_app(_development_settings())
    default_handlers = FastAPI().exception_handlers

    routes = tuple(iter_route_contexts(app.routes))
    application_route_contracts = {
        (route.path, frozenset(route.methods or ()), route.name) for route in routes
    }
    assert application_route_contracts == {
        ("/openapi.json", frozenset({"GET", "HEAD"}), "openapi"),
        ("/docs", frozenset({"GET", "HEAD"}), "swagger_ui_html"),
        ("/health/live", frozenset({"GET"}), get_liveness.__name__),
    }
    assert len(routes) == 3
    health_route = next(route for route in routes if route.path == "/health/live")
    assert health_route.endpoint is get_liveness
    assert (health_route.methods, health_route.name) == (
        {"GET"},
        get_liveness.__name__,
    )
    assert len(app.user_middleware) == 2
    assert app.user_middleware[0].kwargs["dispatch"] is correlation_middleware
    request_logging = app.user_middleware[1].kwargs["dispatch"]
    assert getattr(request_logging, "__name__", None) == "request_logging_middleware"
    expected_handlers = {
        **default_handlers,
        StarletteHTTPException: http_exception_handler,
        RequestValidationError: request_validation_exception_handler,
        Exception: unexpected_exception_handler,
    }
    assert app.exception_handlers == expected_handlers
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []
    assert app.title == "Solar Platform API"
    assert app.version == "1.0.0"
    assert app.openapi_url == "/openapi.json"
    assert app.docs_url == "/docs"
    assert app.swagger_ui_oauth2_redirect_url is None
    assert app.redoc_url is None


def test_factory_configures_logging_without_process_global_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def tracked_configuration() -> None:
        nonlocal calls
        calls += 1

    bound_environments: list[str] = []

    def tracked_binding(environment: str) -> object:
        bound_environments.append(environment)
        return bind_request_logging(environment)

    monkeypatch.setattr(app_module, "configure_logging", tracked_configuration)
    monkeypatch.setattr(app_module, "bind_request_logging", tracked_binding)

    app_module.create_app(Settings(environment=RuntimeEnvironment.PRODUCTION))

    assert calls == 1
    assert bound_environments == ["production"]
