"""Unit tests for project-owned structured logging configuration."""

import json
import logging
from collections.abc import Iterator
from typing import TextIO, cast

import pytest

import solar_platform.logging_config as logging_config

pytestmark = pytest.mark.unit

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _UnsafeValue:
    def __str__(self) -> str:
        raise AssertionError("unsafe value must not be stringified")

    def __repr__(self) -> str:
        raise AssertionError("unsafe value must not be represented")


class _FailingFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def format(self, _record: logging.LogRecord) -> str:
        self.calls += 1
        raise RuntimeError("formatter failed")


@pytest.fixture
def isolated_project_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger(logging_config.PROJECT_LOGGER_NAME)
    original_handlers = tuple(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    original_disabled = logger.disabled
    for handler in original_handlers:
        logger.removeHandler(handler)
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


def _record(*, event: object, environment: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "solar_platform.test",
        logging.INFO,
        __file__,
        1,
        "fixed-safe-message",
        (),
        None,
    )
    record.event = event
    record.environment = environment
    return record


def test_formatter_emits_exact_reviewed_json_schema() -> None:
    record = _record(event="http_request_completed", environment="production")
    record.created = 0.0
    record.correlation_id = VALID_UUID4
    record.http_method = "GET"
    record.http_status = 200
    record.unreviewed = "must-not-appear"

    output = logging_config._JsonLogFormatter().format(record)

    assert json.loads(output) == {
        "schema_version": 1,
        "timestamp": "1970-01-01T00:00:00.000Z",
        "level": "info",
        "service": "solar-platform-backend",
        "environment": "production",
        "event": "http_request_completed",
        "correlation_id": VALID_UUID4,
        "http_method": "GET",
        "http_status": 200,
    }
    assert "fixed-safe-message" not in output
    assert "must-not-appear" not in output
    assert "\n" not in output


def test_formatter_uses_safe_fallbacks_without_reflecting_malformed_values() -> None:
    unsafe = _UnsafeValue()
    record = _record(event="http_request_completed", environment=unsafe)
    record.created = float("nan")
    record.correlation_id = unsafe
    record.http_method = unsafe
    record.http_status = unsafe
    record.exception_type = unsafe
    record.unreviewed = unsafe

    output = logging_config._JsonLogFormatter().format(record)

    assert json.loads(output) == {
        "schema_version": 1,
        "timestamp": "1970-01-01T00:00:00.000Z",
        "level": "info",
        "service": "solar-platform-backend",
        "environment": "unknown",
        "event": "http_request_completed",
        "http_method": "UNKNOWN",
    }

    record.created = 10**10_000
    record.environment = "x" * 10_000
    record.http_method = "Y" * 10_000
    oversized_output = json.loads(logging_config._JsonLogFormatter().format(record))
    assert oversized_output["timestamp"] == "1970-01-01T00:00:00.000Z"
    assert oversized_output["environment"] == "unknown"
    assert oversized_output["http_method"] == "UNKNOWN"


def test_formatter_replaces_unknown_event_and_unsafe_exception_type() -> None:
    unknown = _record(event="unreviewed", environment="test")
    unknown.created = 0
    failed = _record(event="http_request_failed", environment="development")
    failed.created = 0
    failed.http_method = "BAD METHOD"
    failed.exception_type = "unsafe.exception"

    unknown_output = json.loads(logging_config._JsonLogFormatter().format(unknown))
    failed_output = json.loads(logging_config._JsonLogFormatter().format(failed))

    assert unknown_output["event"] == "unknown_event"
    assert "http_method" not in unknown_output
    assert failed_output["http_method"] == "UNKNOWN"
    assert failed_output["exception_type"] == "Exception"


def test_configuration_is_idempotent_and_preserves_caller_handlers(
    isolated_project_logger: logging.Logger,
) -> None:
    root = logging.getLogger()
    original_root_handlers = tuple(root.handlers)
    original_root_level = root.level
    caller_handler = _CaptureHandler()
    isolated_project_logger.addHandler(caller_handler)

    logging_config.configure_logging()
    first_project_handler = next(
        handler
        for handler in isolated_project_logger.handlers
        if isinstance(handler, logging_config._ProjectLogHandler)
    )
    logging_config.configure_logging()

    project_handlers = [
        handler
        for handler in isolated_project_logger.handlers
        if isinstance(handler, logging_config._ProjectLogHandler)
    ]
    assert project_handlers != [first_project_handler]
    assert len(project_handlers) == 1
    assert caller_handler in isolated_project_logger.handlers
    assert isolated_project_logger.propagate is False
    assert isolated_project_logger.level == logging.INFO
    assert tuple(root.handlers) == original_root_handlers
    assert root.level == original_root_level
    assert "environment" not in vars(project_handlers[0])
    formatter = cast(logging.Formatter, project_handlers[0].formatter)
    assert "environment" not in vars(formatter)


def test_configured_handler_writes_one_json_line_to_stderr(
    isolated_project_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logging_config.configure_logging()
    child = logging.getLogger("solar_platform.test")

    child.info(
        "http_request_completed",
        extra={
            "event": "http_request_completed",
            "environment": "test",
            "correlation_id": VALID_UUID4,
            "http_method": "GET",
            "http_status": 204,
        },
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["http_status"] == 204
    handler = next(
        handler
        for handler in isolated_project_logger.handlers
        if isinstance(handler, logging_config._ProjectLogHandler)
    )
    assert cast(logging.StreamHandler[TextIO], handler).stream is not None


def test_project_handler_suppresses_formatter_failure_without_fallback_output(
    isolated_project_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logging_config.configure_logging()
    handler = next(
        handler
        for handler in isolated_project_logger.handlers
        if isinstance(handler, logging_config._ProjectLogHandler)
    )
    formatter = _FailingFormatter()
    handler.setFormatter(formatter)

    logging.getLogger("solar_platform.test").info("http_request_completed")

    captured = capsys.readouterr()
    assert formatter.calls == 1
    assert captured.out == ""
    assert captured.err == ""
