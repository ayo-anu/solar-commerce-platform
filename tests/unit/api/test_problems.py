"""Unit tests for stable disclosure-safe Problem Details responses."""

import asyncio
import json
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from solar_platform.api.correlation import REQUEST_ID_HEADER
from solar_platform.api.problems import (
    MAX_BODY_LOCATION_DEPTH,
    MAX_JSON_POINTER_LENGTH,
    MAX_LOCATION_INDEX,
    MAX_LOCATION_SEGMENT_LENGTH,
    MAX_PARAMETER_NAME_LENGTH,
    MAX_VALIDATION_ISSUES,
    PROBLEM_MEDIA_TYPE,
    ProblemDetails,
    http_exception_handler,
    request_validation_exception_handler,
    unexpected_exception_handler,
)

pytestmark = pytest.mark.unit

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"
SENTINEL = "DO-NOT-DISCLOSE-7fb8bc"


def _request(
    headers: list[tuple[bytes, bytes]] | None = None,
    *,
    path: str = "/",
    query_string: bytes = b"",
) -> Request:
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": query_string,
            "root_path": "",
            "headers": headers or [],
            "client": ("test", 1),
            "server": ("test", 80),
            "state": {},
        },
    )
    return Request(scope)


def _body(response: JSONResponse) -> dict[str, Any]:
    raw_body = response.body
    parsed = json.loads(bytes(raw_body))
    assert isinstance(parsed, dict)
    return cast(dict[str, Any], parsed)


def _http_problem(
    status: int, *, headers: dict[str, str] | None = None, detail: str = SENTINEL
) -> tuple[JSONResponse, dict[str, Any]]:
    request = _request([(b"x-request-id", VALID_UUID4.encode("ascii"))])
    exception = StarletteHTTPException(status, detail=detail, headers=headers)
    response = asyncio.run(http_exception_handler(request, exception))
    return response, _body(response)


@pytest.mark.parametrize(
    ("status", "title", "detail", "headers"),
    (
        (400, "Bad Request", "The request could not be processed.", None),
        (
            401,
            "Unauthorized",
            "Authentication is required.",
            {"WWW-Authenticate": "Bearer"},
        ),
        (403, "Forbidden", "The request is not permitted.", None),
        (404, "Not Found", "The requested resource was not found.", None),
        (
            405,
            "Method Not Allowed",
            "The request method is not allowed for this resource.",
            {"Allow": "GET"},
        ),
        (
            409,
            "Conflict",
            "The request conflicts with the current resource state.",
            None,
        ),
        (412, "Precondition Failed", "A request precondition was not met.", None),
        (
            415,
            "Unsupported Media Type",
            "The request media type is not supported.",
            None,
        ),
        (422, "Unprocessable Content", "The request content is invalid.", None),
        (
            429,
            "Too Many Requests",
            "Too many requests were received.",
            {"Retry-After": "10"},
        ),
        (
            500,
            "Internal Server Error",
            "The server could not complete the request.",
            None,
        ),
        (
            503,
            "Service Unavailable",
            "The service is temporarily unavailable.",
            {"Retry-After": "10"},
        ),
    ),
)
def test_reviewed_http_status_profiles_are_stable(
    status: int, title: str, detail: str, headers: dict[str, str] | None
) -> None:
    response, body = _http_problem(status, headers=headers)

    assert response.status_code == status
    assert response.media_type == PROBLEM_MEDIA_TYPE
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body == {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": VALID_UUID4,
    }
    assert response.headers[REQUEST_ID_HEADER] == VALID_UUID4
    for name, value in (headers or {}).items():
        assert response.headers[name] == value
    assert SENTINEL not in bytes(response.body).decode()


def test_problem_models_are_frozen_and_forbid_extras() -> None:
    problem = ProblemDetails(
        title="Bad Request",
        status=400,
        detail="The request could not be processed.",
        correlation_id=VALID_UUID4,
    )

    with pytest.raises(ValidationError):
        problem.title = "changed"
    with pytest.raises(ValidationError):
        ProblemDetails.model_validate(
            {**problem.model_dump(), "code": "unreviewed-problem-code"}
        )


def test_http_exception_preserves_only_case_insensitive_approved_header() -> None:
    response, body = _http_problem(
        401,
        headers={
            "www-authenticate": "Bearer realm=solar",
            "X-Request-ID": "attacker-controlled",
            "X-Internal": SENTINEL,
        },
    )

    assert response.headers["WWW-Authenticate"] == "Bearer realm=solar"
    assert response.headers[REQUEST_ID_HEADER] == VALID_UUID4
    assert "X-Internal" not in response.headers
    assert SENTINEL not in json.dumps(body)


@pytest.mark.parametrize("status", (401, 405))
def test_missing_mandatory_http_header_collapses_to_sanitized_500(status: int) -> None:
    response, body = _http_problem(status)

    assert response.status_code == 500
    assert body["title"] == "Internal Server Error"
    assert body["status"] == 500


@pytest.mark.parametrize("status", (200, 499))
def test_invalid_or_non_error_http_status_collapses_to_500(status: int) -> None:
    response, body = _http_problem(status)

    assert response.status_code == 500
    assert body["status"] == 500
    assert body["detail"] == "The server could not complete the request."


def test_malformed_json_maps_to_fixed_400_without_disclosure() -> None:
    error = {
        "type": "json_invalid",
        "loc": ("body", 1),
        "msg": SENTINEL,
        "input": SENTINEL,
        "ctx": {"error": SENTINEL},
        "url": f"https://invalid/{SENTINEL}",
    }
    exception = RequestValidationError([error], body={"secret": SENTINEL})
    response = asyncio.run(request_validation_exception_handler(_request(), exception))
    body = _body(response)

    assert response.status_code == 400
    assert body["detail"] == "The request body is not valid JSON."
    assert body["errors"] == [
        {
            "code": "invalid_json",
            "detail": "The request body is not valid JSON.",
            "pointer": "",
        }
    ]
    assert SENTINEL not in bytes(response.body).decode()


def test_semantic_validation_maps_stable_codes_in_original_order() -> None:
    exception = RequestValidationError(
        [
            {"type": "missing", "loc": ("body", "name"), "msg": SENTINEL},
            {"type": "string_type", "loc": ("query", "limit"), "input": SENTINEL},
        ],
        body=SENTINEL,
    )
    response = asyncio.run(request_validation_exception_handler(_request(), exception))
    body = _body(response)

    assert response.status_code == 422
    assert body["errors"] == [
        {
            "code": "required",
            "detail": "This value is required.",
            "pointer": "/name",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "location": "query",
            "parameter": "limit",
        },
    ]
    assert SENTINEL not in bytes(response.body).decode()


@pytest.mark.parametrize(
    ("location", "expected"),
    (
        (("body",), {"pointer": ""}),
        (("body", "a/b", "x~y", 3), {"pointer": "/a~1b/x~0y/3"}),
        (("path", "item-id"), {"location": "path", "parameter": "item-id"}),
        (("query", "page.size"), {"location": "query", "parameter": "page.size"}),
        (("header", "x-token"), {"location": "header", "parameter": "x-token"}),
        (("cookie", "session_id"), {"location": "cookie", "parameter": "session_id"}),
    ),
)
def test_safe_locations_are_emitted(
    location: tuple[object, ...], expected: dict[str, str]
) -> None:
    exception = RequestValidationError([{"type": "value_error", "loc": location}])
    response = asyncio.run(request_validation_exception_handler(_request(), exception))
    issue = cast(list[dict[str, Any]], _body(response)["errors"])[0]

    for name, value in expected.items():
        assert issue[name] == value


class _UnstringifiableLocation:
    def __str__(self) -> str:
        raise AssertionError("unsafe location objects must not be stringified")


@pytest.mark.parametrize(
    "location",
    (
        ("body", "x" * (MAX_LOCATION_SEGMENT_LENGTH + 1)),
        ("body", *("x" for _ in range(MAX_BODY_LOCATION_DEPTH + 1))),
        ("body", "contains space"),
        ("body", "caf\N{LATIN SMALL LETTER E WITH ACUTE}"),
        ("body", -1),
        ("body", True),
        ("body", MAX_LOCATION_INDEX + 1),
        ("body", _UnstringifiableLocation()),
        ("body", *("/" * MAX_LOCATION_SEGMENT_LENGTH for _ in range(5))),
        ("query", "x" * (MAX_PARAMETER_NAME_LENGTH + 1)),
        ("query", "name", "extra"),
        ("unexpected", "name"),
    ),
)
def test_unsafe_locations_are_omitted_without_reflection(
    location: tuple[object, ...],
) -> None:
    exception = RequestValidationError([{"type": "value_error", "loc": location}])
    response = asyncio.run(request_validation_exception_handler(_request(), exception))
    issue = cast(list[dict[str, Any]], _body(response)["errors"])[0]

    assert "pointer" not in issue
    assert "location" not in issue
    assert "parameter" not in issue


def test_location_constants_retain_reviewed_values() -> None:
    assert MAX_BODY_LOCATION_DEPTH == 8
    assert MAX_LOCATION_SEGMENT_LENGTH == 64
    assert MAX_JSON_POINTER_LENGTH == 512
    assert MAX_LOCATION_INDEX == 2_147_483_647
    assert MAX_PARAMETER_NAME_LENGTH == 64


def test_validation_issues_are_capped_without_reflecting_remainder() -> None:
    errors = [
        {"type": "value_error", "loc": ("body", f"field{index}")}
        for index in range(MAX_VALIDATION_ISSUES)
    ]
    errors.append({"type": "value_error", "loc": ("body", SENTINEL), "msg": SENTINEL})
    exception = RequestValidationError(errors, body=SENTINEL)
    response = asyncio.run(request_validation_exception_handler(_request(), exception))
    body = _body(response)

    assert len(body["errors"]) == MAX_VALIDATION_ISSUES
    assert body["errors_truncated"] is True
    assert SENTINEL not in bytes(response.body).decode()


@pytest.mark.parametrize("preestablished", (False, True))
def test_unexpected_500_has_matching_safe_correlation_and_no_disclosure(
    preestablished: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    request = _request(
        [(b"x-secret", SENTINEL.encode("ascii"))],
        path=f"/{SENTINEL}",
        query_string=f"secret={SENTINEL}".encode(),
    )
    if preestablished:
        request.state.correlation_id = VALID_UUID4
    response = asyncio.run(
        unexpected_exception_handler(request, RuntimeError(SENTINEL))
    )
    body = _body(response)

    assert response.status_code == 500
    assert body["detail"] == "The server could not complete the request."
    assert response.headers[REQUEST_ID_HEADER] == body["correlation_id"]
    if preestablished:
        assert body["correlation_id"] == VALID_UUID4
    assert SENTINEL not in bytes(response.body).decode()
    assert SENTINEL not in repr(response)
    assert capsys.readouterr() == ("", "")
