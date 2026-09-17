"""Application composition root."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from solar_platform.api.correlation import correlation_middleware
from solar_platform.api.health import router as health_router
from solar_platform.api.problems import register_problem_handlers
from solar_platform.settings import Settings, load_settings


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Validate configuration and construct a fresh application."""
    _validated_settings = settings if settings is not None else load_settings()
    app = FastAPI(
        title="Solar Platform API",
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        swagger_ui_oauth2_redirect_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.middleware("http")(correlation_middleware)
    register_problem_handlers(app)
    app.include_router(health_router)
    return app
