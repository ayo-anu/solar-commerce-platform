"""Unit tests for safe best-effort request logging."""

import asyncio
import logging
from collections.abc import Iterator

import pytest
from fastapi import Request, Response

from solar_platform.api.request_logging import (
    RequestHandler,
    RequestLoggingMiddleware,
    bind_request_logging,
)
from solar_platform.logging_config import PROJECT_LOGGER_NAME

pytestmark = pytest.mark.unit

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"
SENTINEL = "DO-NOT-LOG-RAW-RECORD-c3e4ae"


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _LoggingEmissionError(Exception):
    pass


class _FailingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def emit(self, _record: logging.LogRecord) -> None:
        self.calls += 1
        raise _LoggingEmissionError("logging failed")


class _LoggingBaseError(BaseException):
    pass


class _BaseFailingHandler(logging.Handler):
    def emit(self, _record: logging.LogRecord) -> None:
        raise _LoggingBaseError


@pytest.fixture
def isolated_project_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger(PROJECT_LOGGER_NAME)
    original_handlers = tuple(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    original_disabled = logger.disabled
    for handler in original_handlers:
        logger.removeHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.disabled = False
    try:
        yield logger
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            if handler not in original_handlers:
                handler.close()
        for handler in original_handlers:
            logger.addHandler(handler)
        logger.setLevel(original_level)
        logger.propagate = original_propagate
        logger.disabled = original_disabled


def _request(method: object = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": f"/unsafe/{SENTINEL}",
            "raw_path": f"/unsafe/{SENTINEL}".encode(),
            "query_string": f"secret={SENTINEL}".encode(),
            "headers": [
                (b"x-request-id", VALID_UUID4.encode()),
                (b"x-secret", SENTINEL.encode()),
                (b"cookie", f"secret={SENTINEL}".encode()),
            ],
            "server": ("internal.example", 443),
            "client": (SENTINEL, 1234),
            "root_path": "",
        }
    )


async def _invoke(
    middleware: RequestLoggingMiddleware,
    request: Request,
    downstream: RequestHandler,
) -> Response:
    return await middleware(request, downstream)


def test_completed_record_contains_only_safe_event_values(
    isolated_project_logger: logging.Logger,
) -> None:
    capture = _CaptureHandler()
    isolated_project_logger.addHandler(capture)
    middleware = bind_request_logging("test")
    expected_response = Response(status_code=204)

    async def downstream(_request: Request) -> Response:
        return expected_response

    response = asyncio.run(_invoke(middleware, _request(), downstream))

    assert response is expected_response
    assert len(capture.records) == 1
    record = capture.records[0]
    assert record.msg == "http_request_completed"
    assert record.levelno == logging.INFO
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    values = vars(record)
    assert values["environment"] == "test"
    assert values["correlation_id"] == VALID_UUID4
    assert values["http_method"] == "GET"
    assert values["http_status"] == 204
    assert SENTINEL not in repr(vars(record))


def test_failure_record_excludes_request_and_exception_sensitive_data(
    isolated_project_logger: logging.Logger,
) -> None:
    capture = _CaptureHandler()
    isolated_project_logger.addHandler(capture)
    middleware = bind_request_logging("production")
    expected_error = RuntimeError(f"exception-{SENTINEL}")
    request = _request()

    async def downstream(_request: Request) -> Response:
        raise expected_error

    with pytest.raises(RuntimeError) as captured_error:
        asyncio.run(_invoke(middleware, request, downstream))

    assert captured_error.value is expected_error
    assert len(capture.records) == 1
    record = capture.records[0]
    assert record.msg == "http_request_failed"
    assert record.levelno == logging.ERROR
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None
    values = vars(record)
    assert values["environment"] == "production"
    assert values["correlation_id"] == VALID_UUID4
    assert values["http_method"] == "GET"
    assert values["exception_type"] == "RuntimeError"
    assert all(value is not request for value in vars(record).values())
    assert all(value is not expected_error for value in vars(record).values())
    assert SENTINEL not in repr(vars(record))


def test_logging_failure_cannot_change_successful_response_or_recurse(
    isolated_project_logger: logging.Logger,
) -> None:
    failing_handler = _FailingHandler()
    isolated_project_logger.addHandler(failing_handler)
    middleware = bind_request_logging("test")
    expected_response = Response(status_code=200)

    async def downstream(_request: Request) -> Response:
        return expected_response

    response = asyncio.run(_invoke(middleware, _request(), downstream))

    assert response is expected_response
    assert failing_handler.calls == 1


def test_logging_failure_preserves_exact_downstream_exception_with_bare_reraise(
    isolated_project_logger: logging.Logger,
) -> None:
    failing_handler = _FailingHandler()
    isolated_project_logger.addHandler(failing_handler)
    middleware = bind_request_logging("test")
    expected_error = LookupError("original application failure")

    async def downstream(_request: Request) -> Response:
        raise expected_error

    with pytest.raises(LookupError) as captured_error:
        asyncio.run(_invoke(middleware, _request(), downstream))

    assert captured_error.value is expected_error
    assert failing_handler.calls == 1


def test_logging_base_exception_is_not_caught(
    isolated_project_logger: logging.Logger,
) -> None:
    isolated_project_logger.addHandler(_BaseFailingHandler())
    middleware = bind_request_logging("test")

    async def downstream(_request: Request) -> Response:
        return Response(status_code=200)

    with pytest.raises(_LoggingBaseError):
        asyncio.run(_invoke(middleware, _request(), downstream))


def test_invalid_bound_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="validated environment"):
        bind_request_logging(SENTINEL)
