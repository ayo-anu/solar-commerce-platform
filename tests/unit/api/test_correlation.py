"""Unit tests for request correlation selection and propagation."""

import asyncio
import importlib
import uuid
from typing import cast

import pytest
from fastapi import Request, Response
from starlette.types import Scope

import solar_platform.api.correlation as correlation

pytestmark = pytest.mark.unit

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "root_path": "",
            "headers": headers or [],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {},
        },
    )
    return Request(scope)


def _assert_canonical_uuid4(value: str) -> None:
    parsed = uuid.UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value


def test_missing_request_id_generates_and_stores_uuid4() -> None:
    request = _request()

    selected = correlation.ensure_correlation_id(request)

    _assert_canonical_uuid4(selected)
    assert request.state.correlation_id == selected


@pytest.mark.parametrize("header_name", (b"x-request-id", b"X-Request-ID"))
def test_one_canonical_uuid4_is_preserved(header_name: bytes) -> None:
    request = _request([(header_name, VALID_UUID4.encode("ascii"))])

    assert correlation.ensure_correlation_id(request) == VALID_UUID4
    assert request.state.correlation_id == VALID_UUID4


@pytest.mark.parametrize(
    "values",
    (
        [b""],
        [b"not-a-uuid"],
        [VALID_UUID4.upper().encode("ascii")],
        [b"123e4567-e89b-12d3-a456-426614174000"],
        ["caf\N{LATIN SMALL LETTER E WITH ACUTE}".encode("utf-8")],
        [VALID_UUID4.encode("ascii"), VALID_UUID4.encode("ascii")],
    ),
)
def test_untrusted_request_ids_are_replaced(values: list[bytes]) -> None:
    request = _request([(b"X-Request-ID", value) for value in values])

    selected = correlation.ensure_correlation_id(request)

    _assert_canonical_uuid4(selected)
    assert selected not in {value.decode("ascii", errors="ignore") for value in values}


def test_middleware_establishes_state_and_overwrites_downstream_header() -> None:
    request = _request([(b"x-request-id", VALID_UUID4.encode("ascii"))])

    async def downstream(received: Request) -> Response:
        assert received.state.correlation_id == VALID_UUID4
        return Response(headers={correlation.REQUEST_ID_HEADER: "downstream-value"})

    response = asyncio.run(correlation.correlation_middleware(request, downstream))

    assert response.headers[correlation.REQUEST_ID_HEADER] == VALID_UUID4


def test_invalid_preexisting_state_is_replaced_safely() -> None:
    request = _request()
    request.state.correlation_id = "unsafe-state-value"

    selected = correlation.ensure_correlation_id(request)

    _assert_canonical_uuid4(selected)
    assert request.state.correlation_id == selected


def test_correlation_import_has_no_runtime_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_uuid() -> uuid.UUID:
        raise AssertionError("module import must not generate correlation state")

    with monkeypatch.context() as context:
        context.setattr(uuid, "uuid4", unexpected_uuid)
        importlib.reload(correlation)
    importlib.reload(correlation)
