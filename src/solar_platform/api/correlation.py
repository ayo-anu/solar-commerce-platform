"""Request correlation selection and response propagation."""

from collections.abc import Awaitable, Callable
from typing import TypeGuard
from uuid import UUID, uuid4

from fastapi import Request, Response

REQUEST_ID_HEADER = "X-Request-ID"
_STATE_ATTRIBUTE = "correlation_id"

RequestHandler = Callable[[Request], Awaitable[Response]]


def _is_canonical_uuid4(value: object) -> TypeGuard[str]:
    if not isinstance(value, str):
        return False
    try:
        value.encode("ascii")
        parsed = UUID(value)
    except (UnicodeEncodeError, ValueError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _select_inbound_correlation_id(request: Request) -> str:
    matching_values: list[bytes] = []
    for name, value in request.scope.get("headers", []):
        if name.lower() == b"x-request-id":
            matching_values.append(value)
    if len(matching_values) == 1:
        try:
            candidate = matching_values[0].decode("ascii")
        except UnicodeDecodeError:
            candidate = ""
        if _is_canonical_uuid4(candidate):
            return candidate
    return str(uuid4())


def ensure_correlation_id(request: Request) -> str:
    """Return one safe request-scoped correlation value, establishing it if needed."""
    existing = getattr(request.state, _STATE_ATTRIBUTE, None)
    if _is_canonical_uuid4(existing):
        return existing
    selected = _select_inbound_correlation_id(request)
    setattr(request.state, _STATE_ATTRIBUTE, selected)
    return selected


async def correlation_middleware(
    request: Request, call_next: RequestHandler
) -> Response:
    """Establish correlation before downstream work and own the response header."""
    correlation_id = ensure_correlation_id(request)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = correlation_id
    return response
