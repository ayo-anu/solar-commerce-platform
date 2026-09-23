"""Application composition root."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool

from solar_platform.api.correlation import correlation_middleware
from solar_platform.api.health import create_readiness_router
from solar_platform.api.health import router as liveness_router
from solar_platform.api.problems import register_problem_handlers
from solar_platform.api.request_logging import bind_request_logging
from solar_platform.database_engine import create_database_engine
from solar_platform.database_runtime import DatabaseRuntime
from solar_platform.logging_config import configure_logging
from solar_platform.settings import (
    DatabaseSettings,
    Settings,
    load_database_settings,
    load_settings,
)


def _application_lifespan(
    runtime: DatabaseRuntime,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            # ASGI lifespan shutdown follows drained framework serving work. The
            # runtime intentionally does not coordinate arbitrary active scopes.
            await run_in_threadpool(runtime.shutdown)

    return lifespan


def _build_database_runtime(settings: DatabaseSettings) -> DatabaseRuntime:
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
    return DatabaseRuntime(engine)


def create_app(
    settings: Settings | None = None,
    database_settings: DatabaseSettings | None = None,
) -> FastAPI:
    """Validate configuration and construct a fresh application."""
    _validated_settings = settings if settings is not None else load_settings()
    _validated_database_settings = (
        database_settings if database_settings is not None else load_database_settings()
    )
    database_runtime = _build_database_runtime(_validated_database_settings)
    app = FastAPI(
        title="Solar Platform API",
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        swagger_ui_oauth2_redirect_url=None,
        redoc_url=None,
        lifespan=_application_lifespan(database_runtime),
    )
    app.middleware("http")(bind_request_logging(_validated_settings.environment.value))
    app.middleware("http")(correlation_middleware)
    register_problem_handlers(app)
    app.include_router(liveness_router)
    app.include_router(create_readiness_router(database_runtime.is_ready))
    configure_logging()
    return app
