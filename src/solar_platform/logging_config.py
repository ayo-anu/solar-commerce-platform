"""Project-owned structured logging output configuration."""

import json
import logging
import math
import re
import sys
from datetime import UTC, datetime
from typing import Final, TextIO, cast, override
from uuid import UUID

PROJECT_LOGGER_NAME: Final = "solar_platform"

_SERVICE_NAME: Final = "solar-platform-backend"
_SCHEMA_VERSION: Final = 1
_FALLBACK_TIMESTAMP: Final = "1970-01-01T00:00:00.000Z"
_KNOWN_ENVIRONMENTS: Final = frozenset({"development", "test", "production"})
_KNOWN_EVENTS: Final = frozenset({"http_request_completed", "http_request_failed"})
_SAFE_METHOD: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Z]{1,32}\Z")
_SAFE_EXCEPTION_TYPE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _timestamp(record: logging.LogRecord) -> str:
    created = record.__dict__.get("created")
    if type(created) not in {int, float}:
        return _FALLBACK_TIMESTAMP
    created_number = cast(int | float, created)
    try:
        finite = math.isfinite(created_number)
    except OverflowError:
        return _FALLBACK_TIMESTAMP
    if not finite:
        return _FALLBACK_TIMESTAMP
    try:
        value = datetime.fromtimestamp(created_number, UTC)
    except (OverflowError, OSError, ValueError):
        return _FALLBACK_TIMESTAMP
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _level(record: logging.LogRecord) -> str:
    level_number = record.__dict__.get("levelno")
    return (
        "error"
        if type(level_number) is int and level_number >= logging.ERROR
        else "info"
    )


def _known_string(
    value: object, approved: frozenset[str], fallback: str, maximum_length: int
) -> str:
    return (
        value
        if type(value) is str and len(value) <= maximum_length and value in approved
        else fallback
    )


def _safe_method(value: object) -> str:
    if type(value) is str and _SAFE_METHOD.fullmatch(value) is not None:
        return value
    return "UNKNOWN"


def _safe_exception_type(value: object) -> str:
    if type(value) is str and _SAFE_EXCEPTION_TYPE.fullmatch(value) is not None:
        return value
    return "Exception"


def _safe_correlation_id(value: object) -> str | None:
    if type(value) is not str or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    if parsed.version != 4 or str(parsed) != value:
        return None
    return value


class _JsonLogFormatter(logging.Formatter):
    """Serialize only the reviewed safe event schema."""

    def format(self, record: logging.LogRecord) -> str:
        values = record.__dict__
        event = _known_string(values.get("event"), _KNOWN_EVENTS, "unknown_event", 32)
        document: dict[str, object] = {
            "schema_version": _SCHEMA_VERSION,
            "timestamp": _timestamp(record),
            "level": _level(record),
            "service": _SERVICE_NAME,
            "environment": _known_string(
                values.get("environment"), _KNOWN_ENVIRONMENTS, "unknown", 16
            ),
            "event": event,
        }

        if event in _KNOWN_EVENTS:
            correlation_id = _safe_correlation_id(values.get("correlation_id"))
            if correlation_id is not None:
                document["correlation_id"] = correlation_id
            document["http_method"] = _safe_method(values.get("http_method"))

        if event == "http_request_completed":
            status = values.get("http_status")
            if type(status) is int and 100 <= status <= 599:
                document["http_status"] = status
        elif event == "http_request_failed":
            document["exception_type"] = _safe_exception_type(
                values.get("exception_type")
            )

        return json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class _ProjectLogHandler(logging.StreamHandler[TextIO]):
    """Identify the handler owned exclusively by this project."""

    @override
    def handleError(self, _record: logging.LogRecord) -> None:
        """Suppress standard-library fallback output after an emission failure."""


def configure_logging() -> None:
    """Install one environment-agnostic JSON handler on the project logger."""
    logger = logging.getLogger(PROJECT_LOGGER_NAME)
    for handler in tuple(logger.handlers):
        if isinstance(handler, _ProjectLogHandler):
            logger.removeHandler(handler)
            handler.close()

    handler = _ProjectLogHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(_JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
