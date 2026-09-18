"""Real ASGI-stack tests for B2.2.6 structured request logging."""

import json
import logging
from collections.abc import Iterator
from typing import Any, cast

import pytest
from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from starlette.testclient import TestClient

from solar_platform.api.correlation import REQUEST_ID_HEADER
from solar_platform.app import create_app
from solar_platform.logging_config import (
    PROJECT_LOGGER_NAME,
    _ProjectLogHandler,
)
from solar_platform.settings import RuntimeEnvironment, Settings

pytestmark = pytest.mark.integration

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"
SECOND_VALID_UUID4 = "223e4567-e89b-42d3-a456-426614174001"
SENTINEL = "DO-NOT-DISCLOSE-LOGGING-9fb7a1"


class _ValidationPayload(BaseModel):
    quantity: int


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _FailingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def emit(self, _record: logging.LogRecord) -> None:
        self.calls += 1
        raise RuntimeError("caller handler failed")


@pytest.fixture(autouse=True)
def isolated_project_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger(PROJECT_LOGGER_NAME)
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


def _app(environment: RuntimeEnvironment) -> FastAPI:
    return create_app(Settings(environment=environment))


def _json_lines(stdout: str, stderr: str) -> list[dict[str, Any]]:
    assert stdout == ""
    return [cast(dict[str, Any], json.loads(line)) for line in stderr.splitlines()]


@pytest.mark.parametrize(
    ("first_environment", "second_environment"),
    (
        (RuntimeEnvironment.PRODUCTION, RuntimeEnvironment.TEST),
        (RuntimeEnvironment.TEST, RuntimeEnvironment.PRODUCTION),
    ),
)
def test_environment_is_bound_per_application_regardless_of_factory_order(
    first_environment: RuntimeEnvironment,
    second_environment: RuntimeEnvironment,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first_app = _app(first_environment)
    second_app = _app(second_environment)
    capsys.readouterr()

    with TestClient(first_app) as first_client:
        first_response = first_client.get(
            "/health/live", headers={REQUEST_ID_HEADER: VALID_UUID4}
        )
    with TestClient(second_app) as second_client:
        second_response = second_client.get(
            "/health/live", headers={REQUEST_ID_HEADER: VALID_UUID4}
        )
    with TestClient(first_app) as first_client:
        repeated_first_response = first_client.get(
            "/health/live", headers={REQUEST_ID_HEADER: VALID_UUID4}
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert repeated_first_response.status_code == 200
    captured = capsys.readouterr()
    records = _json_lines(captured.out, captured.err)
    assert [record["environment"] for record in records] == [
        first_environment.value,
        second_environment.value,
        first_environment.value,
    ]
    assert all(record["correlation_id"] == VALID_UUID4 for record in records)
    assert all(record["event"] == "http_request_completed" for record in records)
    assert all(record["level"] == "info" for record in records)


def test_real_handled_404_and_validation_422_emit_safe_info_completion_records(
    isolated_project_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = _CaptureHandler()
    isolated_project_logger.addHandler(capture)
    app = _app(RuntimeEnvironment.TEST)

    async def validated_route(
        item_id: int, payload: _ValidationPayload
    ) -> dict[str, int]:
        return {"item_id": item_id, "quantity": payload.quantity}

    app.post("/_test/validated/{item_id}", include_in_schema=False)(validated_route)
    capsys.readouterr()

    with TestClient(app) as client:
        not_found_response = client.get(
            f"/missing-{SENTINEL}?secret=query-{SENTINEL}",
            headers={
                REQUEST_ID_HEADER: VALID_UUID4,
                "x-secret": f"header-{SENTINEL}",
                "cookie": f"secret=cookie-{SENTINEL}",
            },
        )
        validation_response = client.post(
            f"/_test/validated/path-{SENTINEL}?secret=query-{SENTINEL}",
            json={"quantity": f"body-{SENTINEL}"},
            headers={
                REQUEST_ID_HEADER: SECOND_VALID_UUID4,
                "x-secret": f"header-{SENTINEL}",
                "cookie": f"secret=cookie-{SENTINEL}",
            },
        )

    assert not_found_response.status_code == 404
    assert validation_response.status_code == 422
    assert not_found_response.headers[REQUEST_ID_HEADER] == VALID_UUID4
    assert validation_response.headers[REQUEST_ID_HEADER] == SECOND_VALID_UUID4
    assert len(capture.records) == 2
    for record, status, correlation_id, method in (
        (capture.records[0], 404, VALID_UUID4, "GET"),
        (capture.records[1], 422, SECOND_VALID_UUID4, "POST"),
    ):
        assert record.msg == "http_request_completed"
        assert record.levelno == logging.INFO
        assert record.args == ()
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.stack_info is None
        values = vars(record)
        assert values["environment"] == "test"
        assert values["correlation_id"] == correlation_id
        assert values["http_method"] == method
        assert values["http_status"] == status
        assert "exception_type" not in values
    assert all(record.msg != "http_request_failed" for record in capture.records)

    captured = capsys.readouterr()
    output_records = _json_lines(captured.out, captured.err)
    assert len(output_records) == 2
    assert [record["event"] for record in output_records] == [
        "http_request_completed",
        "http_request_completed",
    ]
    assert [record["level"] for record in output_records] == ["info", "info"]
    assert [record["http_status"] for record in output_records] == [404, 422]
    assert [record["correlation_id"] for record in output_records] == [
        not_found_response.headers[REQUEST_ID_HEADER],
        validation_response.headers[REQUEST_ID_HEADER],
    ]
    assert all("exception_type" not in record for record in output_records)
    visible_logs = " ".join(
        (
            *(repr(vars(record)) for record in capture.records),
            captured.out,
            captured.err,
        )
    )
    for prohibited_value in (
        SENTINEL,
        "quantity",
        "item_id",
        "value_error.integer_parsing",
    ):
        assert prohibited_value not in visible_logs


def test_handled_503_emits_error_completion_record(
    isolated_project_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = _CaptureHandler()
    isolated_project_logger.addHandler(capture)
    app = _app(RuntimeEnvironment.TEST)

    async def unavailable() -> Response:
        return Response(status_code=503)

    app.get("/_test/unavailable", include_in_schema=False)(unavailable)
    capsys.readouterr()

    with TestClient(app) as client:
        response = client.get(
            "/_test/unavailable", headers={REQUEST_ID_HEADER: VALID_UUID4}
        )

    assert response.status_code == 503
    assert response.headers[REQUEST_ID_HEADER] == VALID_UUID4
    assert len(capture.records) == 1
    record = capture.records[0]
    assert record.msg == "http_request_completed"
    assert record.levelno == logging.ERROR
    assert vars(record)["http_status"] == 503
    assert vars(record)["correlation_id"] == response.headers[REQUEST_ID_HEADER]

    captured = capsys.readouterr()
    output_records = _json_lines(captured.out, captured.err)
    assert len(output_records) == 1
    assert output_records[0]["event"] == "http_request_completed"
    assert output_records[0]["level"] == "error"
    assert output_records[0]["http_status"] == 503
    assert "exception_type" not in output_records[0]


def test_raw_record_and_json_output_exclude_all_request_and_exception_sentinels(
    isolated_project_logger: logging.Logger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture = _CaptureHandler()
    isolated_project_logger.addHandler(capture)
    app = _app(RuntimeEnvironment.PRODUCTION)
    path_sentinel = f"path-{SENTINEL}"
    query_sentinel = f"query-{SENTINEL}"
    header_sentinel = f"header-{SENTINEL}"
    cookie_sentinel = f"cookie-{SENTINEL}"
    body_sentinel = f"body-{SENTINEL}"
    configuration_sentinel = f"configuration-{SENTINEL}"

    async def fail(_request: Request) -> None:
        raise RuntimeError(f"exception-{SENTINEL}-{configuration_sentinel}")

    app.post("/_test/failure/{path_value}", include_in_schema=False)(fail)
    capsys.readouterr()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/_test/failure/{path_sentinel}?value={query_sentinel}",
            content=body_sentinel,
            headers={
                "x-sentinel": header_sentinel,
                "cookie": f"sentinel={cookie_sentinel}",
                REQUEST_ID_HEADER: VALID_UUID4,
            },
        )

    assert response.status_code == 500
    assert response.json()["correlation_id"] == VALID_UUID4
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
    assert values["http_method"] == "POST"
    assert values["exception_type"] == "RuntimeError"

    captured = capsys.readouterr()
    output_records = _json_lines(captured.out, captured.err)
    assert len(output_records) == 1
    assert output_records[0] == {
        "schema_version": 1,
        "timestamp": output_records[0]["timestamp"],
        "level": "error",
        "service": "solar-platform-backend",
        "environment": "production",
        "event": "http_request_failed",
        "correlation_id": VALID_UUID4,
        "http_method": "POST",
        "exception_type": "RuntimeError",
    }
    visible = " ".join((repr(vars(record)), captured.out, captured.err, response.text))
    for sentinel in (
        SENTINEL,
        path_sentinel,
        query_sentinel,
        header_sentinel,
        cookie_sentinel,
        body_sentinel,
        configuration_sentinel,
    ):
        assert sentinel not in visible


def test_failing_caller_handler_cannot_change_liveness_or_trigger_recursion(
    isolated_project_logger: logging.Logger,
) -> None:
    failing_handler = _FailingHandler()
    isolated_project_logger.addHandler(failing_handler)
    app = _app(RuntimeEnvironment.TEST)

    with TestClient(app) as client:
        response = client.get("/health/live", headers={REQUEST_ID_HEADER: VALID_UUID4})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers[REQUEST_ID_HEADER] == VALID_UUID4
    assert failing_handler.calls == 1
    assert failing_handler in isolated_project_logger.handlers
    assert (
        sum(
            isinstance(handler, _ProjectLogHandler)
            for handler in isolated_project_logger.handlers
        )
        == 1
    )
