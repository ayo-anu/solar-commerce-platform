"""Real ASGI-stack tests for the B2.2.5 HTTP runtime foundation."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast
from uuid import UUID

import pytest
from fastapi import Cookie, FastAPI, Header, Request, Response
from httpx2 import Response as ClientResponse
from pydantic import BaseModel, Field
from starlette.testclient import TestClient

import solar_platform.app as app_module
from solar_platform.api.correlation import REQUEST_ID_HEADER
from solar_platform.api.problems import MAX_VALIDATION_ISSUES, PROBLEM_MEDIA_TYPE
from solar_platform.settings import RuntimeEnvironment, Settings

pytestmark = pytest.mark.integration

VALID_UUID4 = "123e4567-e89b-42d3-a456-426614174000"
SENTINEL = "DO-NOT-DISCLOSE-REAL-STACK-7fb8bc"


def _test_app() -> FastAPI:
    return app_module.create_app(Settings(environment=RuntimeEnvironment.TEST))


def _assert_canonical_uuid4(value: str) -> None:
    parsed = UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value


def _problem_body(response: ClientResponse, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = cast(dict[str, Any], response.json())
    assert body["type"] == "about:blank"
    assert body["status"] == status
    assert "code" not in body
    assert "instance" not in body
    assert response.headers[REQUEST_ID_HEADER] == body["correlation_id"]
    _assert_canonical_uuid4(cast(str, body["correlation_id"]))
    return body


def test_liveness_is_minimal_and_correlation_is_edge_owned() -> None:
    app = _test_app()

    async def downstream_header() -> Response:
        return Response(headers={REQUEST_ID_HEADER: "downstream-controlled"})

    app.get("/_test/downstream-header", include_in_schema=False)(downstream_header)

    with TestClient(app) as client:
        generated = client.get("/health/live")
        preserved = client.get("/health/live", headers={REQUEST_ID_HEADER: VALID_UUID4})
        overwritten = client.get(
            "/_test/downstream-header",
            headers={REQUEST_ID_HEADER: VALID_UUID4},
        )

    assert generated.status_code == 200
    assert generated.headers["content-type"] == "application/json"
    assert generated.json() == {"status": "ok"}
    assert set(generated.json()) == {"status"}
    _assert_canonical_uuid4(generated.headers[REQUEST_ID_HEADER])
    assert preserved.json() == {"status": "ok"}
    assert preserved.headers[REQUEST_ID_HEADER] == VALID_UUID4
    assert overwritten.headers[REQUEST_ID_HEADER] == VALID_UUID4


@pytest.mark.parametrize(
    ("method", "path", "status", "title", "detail", "allow"),
    (
        (
            "GET",
            "/missing",
            404,
            "Not Found",
            "The requested resource was not found.",
            None,
        ),
        (
            "POST",
            "/health/live",
            405,
            "Method Not Allowed",
            "The request method is not allowed for this resource.",
            "GET",
        ),
    ),
)
def test_router_failures_use_stable_problem_details(
    method: str,
    path: str,
    status: int,
    title: str,
    detail: str,
    allow: str | None,
) -> None:
    with TestClient(_test_app()) as client:
        response = client.request(
            method, path, headers={REQUEST_ID_HEADER: VALID_UUID4}
        )

    body = _problem_body(response, status)
    assert body == {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": VALID_UUID4,
    }
    if allow is None:
        assert "allow" not in response.headers
    else:
        assert response.headers["allow"] == allow


class ValidationPayload(BaseModel):
    required_name: str
    aliased_value: int = Field(alias="a/b")
    tilde_value: int = Field(alias="x~y")


def test_malformed_json_maps_to_stable_400_problem() -> None:
    app = _test_app()

    async def accept_payload(_payload: ValidationPayload) -> None:
        return None

    app.post("/_test/body", include_in_schema=False)(accept_payload)

    with TestClient(app) as client:
        response = client.post(
            "/_test/body",
            content=b'{"secret":',
            headers={
                "content-type": "application/json",
                REQUEST_ID_HEADER: VALID_UUID4,
            },
        )

    body = _problem_body(response, 400)
    assert body == {
        "type": "about:blank",
        "title": "Bad Request",
        "status": 400,
        "detail": "The request body is not valid JSON.",
        "correlation_id": VALID_UUID4,
        "errors": [
            {
                "code": "invalid_json",
                "detail": "The request body is not valid JSON.",
                "pointer": "",
            }
        ],
    }
    assert "secret" not in response.text


def test_semantic_validation_maps_body_and_transport_locations() -> None:
    app = _test_app()

    async def validate_locations(
        item_id: int,
        limit: int,
        x_token: Annotated[int, Header(alias="x-token")],
        session_id: Annotated[int, Cookie(alias="session-id")],
        payload: ValidationPayload,
    ) -> None:
        return None

    app.post("/_test/locations/{item_id}", include_in_schema=False)(validate_locations)

    with TestClient(app) as client:
        response = client.post(
            "/_test/locations/not-an-int?limit=not-an-int",
            headers={
                "x-token": "not-an-int",
                "cookie": "session-id=not-an-int",
                REQUEST_ID_HEADER: VALID_UUID4,
            },
            json={"a/b": "not-an-int", "x~y": "not-an-int"},
        )

    body = _problem_body(response, 422)
    assert body["title"] == "Unprocessable Content"
    assert body["detail"] == "One or more request values are invalid."
    assert "errors_truncated" not in body
    issues = cast(list[dict[str, Any]], body["errors"])
    assert issues == [
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "location": "path",
            "parameter": "item_id",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "location": "query",
            "parameter": "limit",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "location": "header",
            "parameter": "x-token",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "location": "cookie",
            "parameter": "session-id",
        },
        {
            "code": "required",
            "detail": "This value is required.",
            "pointer": "/required_name",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "pointer": "/a~1b",
        },
        {
            "code": "invalid",
            "detail": "This value is invalid.",
            "pointer": "/x~0y",
        },
    ]
    for unsafe_framework_field in ("msg", "input", "ctx", "url"):
        assert unsafe_framework_field not in response.text


DeepIntegerList = list[list[list[list[list[list[list[list[int]]]]]]]]
OVERSIZED_ALIAS = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class UnsafeLocationPayload(BaseModel):
    oversized: int = Field(
        alias="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    )
    deep: DeepIntegerList


def test_framework_producible_unsafe_locations_are_omitted() -> None:
    app = _test_app()

    async def validate_unsafe_locations(_payload: UnsafeLocationPayload) -> None:
        return None

    app.post("/_test/unsafe-locations", include_in_schema=False)(
        validate_unsafe_locations
    )

    with TestClient(app) as client:
        response = client.post(
            "/_test/unsafe-locations",
            json={"deep": [[[[[[[[SENTINEL]]]]]]]]},
        )

    body = _problem_body(response, 422)
    issues = cast(list[dict[str, Any]], body["errors"])
    assert len(issues) == 2
    assert {issue["code"] for issue in issues} == {"required", "invalid"}
    for issue in issues:
        assert "pointer" not in issue
        assert "location" not in issue
        assert "parameter" not in issue
    assert OVERSIZED_ALIAS not in response.text
    assert SENTINEL not in response.text


class InvalidItem(BaseModel):
    value: int


class InvalidBatch(BaseModel):
    items: list[InvalidItem]


def test_validation_issue_bound_preserves_first_fifty_in_order() -> None:
    app = _test_app()

    async def validate_batch(_payload: InvalidBatch) -> None:
        return None

    app.post("/_test/batch", include_in_schema=False)(validate_batch)
    submitted_items = [{"value": f"invalid-{index}"} for index in range(51)]
    submitted_items[-1]["value"] = SENTINEL

    with TestClient(app) as client:
        response = client.post("/_test/batch", json={"items": submitted_items})

    body = _problem_body(response, 422)
    issues = cast(list[dict[str, Any]], body["errors"])
    assert len(issues) == MAX_VALIDATION_ISSUES
    assert body["errors_truncated"] is True
    assert [issue["pointer"] for issue in issues] == [
        f"/items/{index}/value" for index in range(MAX_VALIDATION_ISSUES)
    ]
    assert all(issue["code"] == "invalid" for issue in issues)
    assert "errors_total" not in body
    assert "errors_remaining" not in body
    assert SENTINEL not in response.text


def test_unexpected_failure_uses_real_server_error_stack_without_disclosure(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = _test_app()
    path_sentinel = f"path-{SENTINEL}"
    query_sentinel = f"query-{SENTINEL}"
    header_sentinel = f"header-{SENTINEL}"
    cookie_sentinel = f"cookie-{SENTINEL}"
    body_sentinel = f"body-{SENTINEL}"
    configuration_sentinel = f"configuration-{SENTINEL}"

    async def fail(_request: Request) -> None:
        raise RuntimeError(f"exception-{SENTINEL}-{configuration_sentinel}")

    app.post("/_test/failure/{path_value}", include_in_schema=False)(fail)

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

    body = _problem_body(response, 500)
    assert body == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "The server could not complete the request.",
        "correlation_id": VALID_UUID4,
    }
    captured = capsys.readouterr()
    visible_surfaces = " ".join(
        (
            response.text,
            repr(response),
            repr(dict(response.headers)),
            captured.out,
            captured.err,
            caplog.text,
        )
    )
    for sentinel in (
        SENTINEL,
        path_sentinel,
        query_sentinel,
        header_sentinel,
        cookie_sentinel,
        body_sentinel,
        configuration_sentinel,
    ):
        assert sentinel not in visible_surfaces


def test_openapi_and_swagger_expose_only_the_liveness_contract() -> None:
    with TestClient(_test_app()) as client:
        schema_response = client.get("/openapi.json")
        docs_response = client.get("/docs")
        redoc_response = client.get("/redoc")
        oauth_response = client.get("/docs/oauth2-redirect")

    assert schema_response.status_code == 200
    schema = cast(dict[str, Any], schema_response.json())
    assert schema["info"] == {"title": "Solar Platform API", "version": "1.0.0"}
    assert set(cast(dict[str, Any], schema["paths"])) == {"/health/live"}
    operation = schema["paths"]["/health/live"]["get"]
    assert operation["operationId"] == "get_liveness"
    assert operation["summary"] == "Check process liveness"
    assert operation["tags"] == ["Health"]
    assert set(operation["responses"]) == {"200"}
    response_content = operation["responses"]["200"]["content"]
    assert set(response_content) == {"application/json"}
    assert response_content["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LivenessResponse"
    }
    liveness_schema = schema["components"]["schemas"]["LivenessResponse"]
    assert liveness_schema["additionalProperties"] is False
    assert liveness_schema["required"] == ["status"]
    assert liveness_schema["properties"] == {
        "status": {
            "const": "ok",
            "title": "Status",
            "type": "string",
        }
    }
    assert "securitySchemes" not in schema.get("components", {})
    assert "servers" not in schema
    assert docs_response.status_code == 200
    assert "/openapi.json" in docs_response.text
    assert "oauth2RedirectUrl" not in docs_response.text
    _problem_body(redoc_response, 404)
    _problem_body(oauth_response, 404)


def test_lifespan_enters_and_exits_once_per_independent_client_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan_spy(_app: FastAPI) -> AsyncIterator[None]:
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(app_module, "_lifespan", lifespan_spy)
    first_app = _test_app()
    second_app = _test_app()

    assert first_app is not second_app
    assert events == []

    with TestClient(first_app) as first_client:
        assert events == ["enter"]
        assert first_client.get("/health/live").status_code == 200
    assert events == ["enter", "exit"]

    with TestClient(second_app) as second_client:
        assert events == ["enter", "exit", "enter"]
        assert second_client.get("/health/live").status_code == 200
    assert events == ["enter", "exit", "enter", "exit"]
