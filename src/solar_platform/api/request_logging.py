"""Disclosure-safe terminal HTTP request logging."""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request, Response

from solar_platform.api.correlation import ensure_correlation_id

RequestHandler = Callable[[Request], Awaitable[Response]]
RequestLoggingMiddleware = Callable[[Request, RequestHandler], Awaitable[Response]]

_LOGGER = logging.getLogger(__name__)
_COMPLETED_EVENT: Final = "http_request_completed"
_FAILED_EVENT: Final = "http_request_failed"
_KNOWN_ENVIRONMENTS: Final = frozenset({"development", "test", "production"})
_SAFE_METHOD: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Z]{1,32}\Z")
_SAFE_EXCEPTION_TYPE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _safe_method(value: object) -> str:
    if type(value) is str and _SAFE_METHOD.fullmatch(value) is not None:
        return value
    return "UNKNOWN"


def _safe_exception_type(exception: Exception) -> str:
    value = type(exception).__name__
    if _SAFE_EXCEPTION_TYPE.fullmatch(value) is not None:
        return value
    return "Exception"


def _emit_completed(
    *, environment: str, correlation_id: str, method: str, status: int
) -> None:
    level = logging.ERROR if status >= 500 else logging.INFO
    _LOGGER.log(
        level,
        _COMPLETED_EVENT,
        extra={
            "event": _COMPLETED_EVENT,
            "environment": environment,
            "correlation_id": correlation_id,
            "http_method": method,
            "http_status": status,
        },
    )


def _emit_failed(
    *,
    environment: str,
    correlation_id: str,
    method: str,
    exception: Exception,
) -> None:
    _LOGGER.error(
        _FAILED_EVENT,
        extra={
            "event": _FAILED_EVENT,
            "environment": environment,
            "correlation_id": correlation_id,
            "http_method": method,
            "exception_type": _safe_exception_type(exception),
        },
    )


def bind_request_logging(environment: str) -> RequestLoggingMiddleware:
    """Bind one validated environment to a best-effort request logger."""
    if environment not in _KNOWN_ENVIRONMENTS:
        raise ValueError("request logging requires a validated environment")

    async def request_logging_middleware(
        request: Request, call_next: RequestHandler
    ) -> Response:
        correlation_id = ensure_correlation_id(request)
        method = _safe_method(request.method)
        try:
            response = await call_next(request)
        except Exception as exception:
            try:
                _emit_failed(
                    environment=environment,
                    correlation_id=correlation_id,
                    method=method,
                    exception=exception,
                )
            except Exception:
                pass
            raise

        try:
            _emit_completed(
                environment=environment,
                correlation_id=correlation_id,
                method=method,
                status=response.status_code,
            )
        except Exception:
            pass
        return response

    return request_logging_middleware
