"""Stable RFC 9457 responses for framework and unexpected HTTP failures."""

import re
from collections.abc import Mapping
from http import HTTPStatus
from typing import Final, Literal, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from solar_platform.api.correlation import REQUEST_ID_HEADER, ensure_correlation_id

PROBLEM_MEDIA_TYPE = "application/problem+json"
MAX_VALIDATION_ISSUES = 50
MAX_BODY_LOCATION_DEPTH = 8
MAX_LOCATION_SEGMENT_LENGTH = 64
MAX_JSON_POINTER_LENGTH = 512
MAX_LOCATION_INDEX = 2_147_483_647
MAX_PARAMETER_NAME_LENGTH = 64

_ABOUT_BLANK: Final = "about:blank"
_SAFE_LOCATION_SEGMENT = re.compile(r"[A-Za-z0-9_.~/\-]+\Z")
_TRANSPORT_SOURCES = frozenset({"path", "query", "header", "cookie"})
TransportLocation = Literal["path", "query", "header", "cookie"]


class ValidationIssue(BaseModel):
    """One stable, disclosure-safe request validation issue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Literal["invalid_json", "required", "invalid"]
    detail: str
    pointer: str | None = None
    location: Literal["path", "query", "header", "cookie"] | None = None
    parameter: str | None = None


class ProblemDetails(BaseModel):
    """The B2.2.4 generic RFC 9457 response body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["about:blank"] = _ABOUT_BLANK
    title: str
    status: int
    detail: str
    correlation_id: str
    errors: tuple[ValidationIssue, ...] | None = None
    errors_truncated: Literal[True] | None = None


_STATUS_PROFILES: Final[dict[int, tuple[str, str]]] = {
    400: ("Bad Request", "The request could not be processed."),
    401: ("Unauthorized", "Authentication is required."),
    403: ("Forbidden", "The request is not permitted."),
    404: ("Not Found", "The requested resource was not found."),
    405: (
        "Method Not Allowed",
        "The request method is not allowed for this resource.",
    ),
    409: ("Conflict", "The request conflicts with the current resource state."),
    412: ("Precondition Failed", "A request precondition was not met."),
    415: ("Unsupported Media Type", "The request media type is not supported."),
    422: ("Unprocessable Content", "The request content is invalid."),
    429: ("Too Many Requests", "Too many requests were received."),
    500: ("Internal Server Error", "The server could not complete the request."),
    503: ("Service Unavailable", "The service is temporarily unavailable."),
}


def _status_profile(status: object) -> tuple[int, str, str]:
    if type(status) is not int or not 400 <= status <= 599:
        status = 500
    try:
        standard_title = HTTPStatus(status).phrase
    except ValueError:
        status = 500
        standard_title = HTTPStatus.INTERNAL_SERVER_ERROR.phrase
    title, detail = _STATUS_PROFILES.get(
        status,
        (
            standard_title,
            "The request could not be processed."
            if status < 500
            else "The server could not complete the request.",
        ),
    )
    return status, title, detail


def _problem_response(
    request: Request,
    status: int,
    title: str,
    detail: str,
    *,
    headers: Mapping[str, str] | None = None,
    errors: tuple[ValidationIssue, ...] | None = None,
    errors_truncated: Literal[True] | None = None,
) -> JSONResponse:
    correlation_id = ensure_correlation_id(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = correlation_id
    problem = ProblemDetails(
        title=title,
        status=status,
        detail=detail,
        correlation_id=correlation_id,
        errors=errors,
        errors_truncated=errors_truncated,
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _sanitized_internal_error(request: Request) -> JSONResponse:
    status, title, detail = _status_profile(500)
    return _problem_response(request, status, title, detail)


def _approved_exception_headers(
    status: int, headers: Mapping[str, str] | None
) -> dict[str, str]:
    if headers is None:
        return {}
    approved_name = {
        401: "WWW-Authenticate",
        405: "Allow",
        429: "Retry-After",
        503: "Retry-After",
    }.get(status)
    if approved_name is None:
        return {}
    for name, value in headers.items():
        if (
            isinstance(name, str)
            and name.lower() == approved_name.lower()
            and isinstance(value, str)
            and value.strip()
        ):
            return {approved_name: value}
    return {}


async def http_exception_handler(
    request: Request, exception: StarletteHTTPException
) -> JSONResponse:
    """Translate Starlette/FastAPI HTTP exceptions without exposing detail."""
    status, title, detail = _status_profile(exception.status_code)
    if status != exception.status_code:
        return _sanitized_internal_error(request)
    headers = _approved_exception_headers(status, exception.headers)
    if status == 401 and "WWW-Authenticate" not in headers:
        return _sanitized_internal_error(request)
    if status == 405 and "Allow" not in headers:
        return _sanitized_internal_error(request)
    return _problem_response(request, status, title, detail, headers=headers)


def _approved_location_segment(value: object, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum_length
        and value.isascii()
        and _SAFE_LOCATION_SEGMENT.fullmatch(value) is not None
    )


def _safe_location(
    raw_location: object,
) -> tuple[str | None, TransportLocation | None, str | None]:
    if not isinstance(raw_location, (tuple, list)):
        return None, None, None
    location = tuple(raw_location)
    if not location or not isinstance(location[0], str):
        return None, None, None

    source = location[0]
    if source == "body":
        segments = location[1:]
        if len(segments) > MAX_BODY_LOCATION_DEPTH:
            return None, None, None
        encoded_segments: list[str] = []
        for segment in segments:
            if type(segment) is int:
                if not 0 <= segment <= MAX_LOCATION_INDEX:
                    return None, None, None
                encoded = str(segment)
            elif _approved_location_segment(segment, MAX_LOCATION_SEGMENT_LENGTH):
                encoded = segment.replace("~", "~0").replace("/", "~1")
            else:
                return None, None, None
            encoded_segments.append(encoded)
        pointer = "" if not encoded_segments else "/" + "/".join(encoded_segments)
        if len(pointer) > MAX_JSON_POINTER_LENGTH:
            return None, None, None
        return pointer, None, None

    if (
        source in _TRANSPORT_SOURCES
        and len(location) == 2
        and _approved_location_segment(location[1], MAX_PARAMETER_NAME_LENGTH)
    ):
        return None, cast(TransportLocation, source), location[1]
    return None, None, None


def _validation_issue(raw_error: object) -> ValidationIssue:
    error_type: object = None
    raw_location: object = None
    if isinstance(raw_error, Mapping):
        error_type = raw_error.get("type")
        raw_location = raw_error.get("loc")
    if error_type == "missing":
        code: Literal["required", "invalid"] = "required"
        detail = "This value is required."
    else:
        code = "invalid"
        detail = "This value is invalid."
    pointer, location, parameter = _safe_location(raw_location)
    return ValidationIssue(
        code=code,
        detail=detail,
        pointer=pointer,
        location=location,
        parameter=parameter,
    )


async def request_validation_exception_handler(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    """Map structured framework validation failures to stable safe issues."""
    raw_errors = exception.errors()
    if any(
        isinstance(error, Mapping) and error.get("type") == "json_invalid"
        for error in raw_errors
    ):
        issue = ValidationIssue(
            code="invalid_json",
            detail="The request body is not valid JSON.",
            pointer="",
        )
        return _problem_response(
            request,
            400,
            "Bad Request",
            "The request body is not valid JSON.",
            errors=(issue,),
        )

    errors = tuple(
        _validation_issue(error) for error in raw_errors[:MAX_VALIDATION_ISSUES]
    )
    return _problem_response(
        request,
        422,
        "Unprocessable Content",
        "One or more request values are invalid.",
        errors=errors,
        errors_truncated=True if len(raw_errors) > MAX_VALIDATION_ISSUES else None,
    )


async def unexpected_exception_handler(
    request: Request, _exception: Exception
) -> JSONResponse:
    """Return a fixed sanitized response for an unexpected failure."""
    return _sanitized_internal_error(request)


def register_problem_handlers(app: FastAPI) -> None:
    """Register the reviewed HTTP-edge exception translations."""
    app.add_exception_handler(
        StarletteHTTPException, cast(ExceptionHandler, http_exception_handler)
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, request_validation_exception_handler),
    )
    app.add_exception_handler(Exception, unexpected_exception_handler)
