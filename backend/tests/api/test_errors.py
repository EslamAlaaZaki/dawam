"""Every error response has the shape {"error": {"code", "message", "details"}}."""

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from dawam.platform.errors import ApiError


class NewItem(BaseModel):
    name: str
    quantity: int


@pytest.fixture(autouse=True)
def probe_routes(app: FastAPI) -> None:
    """Routes that fail on purpose, mounted under /api/v1 like real module routers."""
    router = APIRouter(prefix="/api/v1/_probe")

    @router.post("/items/{item_id}")
    def create_item(item_id: int, item: NewItem) -> NewItem:
        return item

    @router.get("/conflict")
    def conflict() -> None:
        message = "The item was changed by someone else."
        raise ApiError(409, "stale_version", message, {"version": 3})

    @router.get("/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Not your Workspace.")

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret connection string leaked here")

    app.include_router(router)


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]
    return body["error"]


def test_unknown_api_route_is_a_404_error(anonymous_client):
    response = anonymous_client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"


def test_unknown_route_outside_the_api_uses_the_same_shape(anonymous_client):
    response = anonymous_client.get("/does-not-exist")

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"


@pytest.mark.parametrize("path", ["/", "/login", "/index.html", "/_next/static/app.js"])
def test_the_app_serves_no_web_ui(anonymous_client, path):
    """The pages are the Next.js `web` service's; the app answers them like any unknown path."""
    response = anonymous_client.get(path)

    assert response.status_code == 404
    assert error_of(response)["code"] == "not_found"


def test_wrong_method_is_a_405_error(anonymous_client):
    response = anonymous_client.delete("/api/v1/version")

    assert response.status_code == 405
    assert error_of(response)["code"] == "method_not_allowed"


def test_validation_errors_list_each_invalid_field(anonymous_client):
    response = anonymous_client.post("/api/v1/_probe/items/abc", json={"quantity": "many"})

    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "validation_error"
    fields = {tuple(f["loc"]): f for f in error["details"]["fields"]}
    assert set(fields) == {("path", "item_id"), ("body", "name"), ("body", "quantity")}
    assert all(f["message"] and f["type"] for f in fields.values())


def test_malformed_json_body_is_a_validation_error(anonymous_client):
    response = anonymous_client.post(
        "/api/v1/_probe/items/1",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "validation_error"


def test_api_error_carries_its_code_message_and_details(anonymous_client):
    response = anonymous_client.get("/api/v1/_probe/conflict")

    assert response.status_code == 409
    assert error_of(response) == {
        "code": "stale_version",
        "message": "The item was changed by someone else.",
        "details": {"version": 3},
    }


def test_http_exception_is_wrapped_in_the_error_shape(anonymous_client):
    response = anonymous_client.get("/api/v1/_probe/forbidden")

    assert response.status_code == 403
    assert error_of(response) == {
        "code": "forbidden",
        "message": "Not your Workspace.",
        "details": {},
    }


def test_unhandled_exception_is_a_500_error_without_internals(anonymous_client):
    response = anonymous_client.get("/api/v1/_probe/boom")

    assert response.status_code == 500
    error = error_of(response)
    assert error["code"] == "internal_error"
    assert "secret" not in response.text


def test_openapi_documents_the_error_shape(anonymous_client):
    spec = anonymous_client.get("/api/v1/openapi.json").json()

    assert set(spec["components"]["schemas"]["ErrorResponse"]["properties"]) == {"error"}
    real_paths = {p: ops for p, ops in spec["paths"].items() if "/_probe/" not in p}
    assert real_paths
    for path, operations in real_paths.items():
        for method, operation in operations.items():
            responses = operation["responses"]
            for status in ("default", "422"):
                if status in responses:
                    schema = responses[status]["content"]["application/json"]["schema"]
                    assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (
                        f"{method.upper()} {path} {status}"
                    )
            assert "default" in responses, f"{method.upper()} {path}"
