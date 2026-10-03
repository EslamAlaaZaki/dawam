"""Every response carries the security headers of the spec's baseline (§8.4).

API JSON, the served frontend (HTML and static files), the API docs page and error
responses all get a Content-Security-Policy with ``frame-ancestors 'none'``,
``X-Content-Type-Options: nosniff`` and a strict ``Referrer-Policy``. HSTS is sent
only over HTTPS.
"""

import base64
import hashlib
import re

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from dawam.app import create_app
from dawam.platform.api_docs import install_api_docs

APP_CSP_DIRECTIVES = {
    "default-src": ["'self'"],
    "img-src": ["'self'", "data:"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
}


def csp_of(response) -> dict[str, list[str]]:
    policy = response.headers["Content-Security-Policy"]
    directives = {}
    for part in policy.split(";"):
        name, *sources = part.split()
        directives[name] = sources
    return directives


def assert_baseline_headers(response) -> None:
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert csp_of(response)["frame-ancestors"] == ["'none'"]


@pytest.fixture
def probe_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/_probe")

    @router.get("/items/{item_id}")
    def item(item_id: int) -> dict:
        return {"id": item_id}

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("unhandled")

    app.include_router(router)


@pytest.fixture
def frontend_client(tmp_path, settings, services):
    (tmp_path / "index.html").write_text("<!doctype html><title>DAWAM</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    settings = settings.model_copy(update={"frontend_dist": tmp_path})
    with TestClient(create_app(settings, services=services)) as client:
        yield client


@pytest.mark.usefixtures("probe_routes")
@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/v1/version", 200),
        ("GET", "/healthz", 200),
        ("GET", "/api/v1/openapi.json", 200),
        ("GET", "/api/v1/_probe/items/7", 200),
        ("GET", "/api/v1/does-not-exist", 404),
        ("GET", "/does-not-exist", 404),
        ("DELETE", "/api/v1/version", 405),
        ("GET", "/api/v1/_probe/items/abc", 422),
        ("GET", "/api/v1/_probe/boom", 500),
    ],
)
def test_api_and_error_responses_carry_the_security_headers(anonymous_client, method, path, status):
    response = anonymous_client.request(method, path)

    assert response.status_code == status
    assert_baseline_headers(response)
    assert csp_of(response) == APP_CSP_DIRECTIVES


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/", 200),
        ("/index.html", 200),
        ("/assets/app.js", 200),
        ("/assets/missing.js", 404),
        ("/api/v1/version", 200),
    ],
)
def test_frontend_responses_carry_the_security_headers(frontend_client, path, status):
    response = frontend_client.get(path)

    assert response.status_code == status
    assert_baseline_headers(response)
    assert csp_of(response) == APP_CSP_DIRECTIVES


def test_the_app_policy_allows_no_inline_or_third_party_code(anonymous_client):
    policy = anonymous_client.get("/api/v1/version").headers["Content-Security-Policy"]

    assert "unsafe" not in policy
    assert "http" not in policy


def test_the_api_docs_page_works_under_its_own_strict_policy(anonymous_client):
    response = anonymous_client.get("/api/v1/docs")

    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text
    assert_baseline_headers(response)
    csp = csp_of(response)
    assert csp["default-src"] == ["'none'"]
    assert "unsafe" not in response.headers["Content-Security-Policy"]
    # Swagger UI comes from the CDN; its one inline bootstrap script is allowed by hash.
    inline_scripts = re.findall(r"<script>(.*?)</script>", response.text, flags=re.DOTALL)
    assert len(inline_scripts) == 1
    digest = base64.b64encode(hashlib.sha256(inline_scripts[0].encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp["script-src"]
    script_urls = re.findall(r'<script src="([^"]+)"', response.text)
    css_urls = re.findall(r'rel="stylesheet" href="([^"]+)"', response.text)
    assert script_urls and css_urls
    assert all(url.startswith("https://cdn.jsdelivr.net/") for url in script_urls + css_urls)
    assert "https://cdn.jsdelivr.net" in csp["script-src"]
    assert "https://cdn.jsdelivr.net" in csp["style-src"]
    assert csp["connect-src"] == ["'self'"]


def test_the_api_docs_page_does_not_send_the_spec_to_an_outside_validator(anonymous_client):
    assert '"validatorUrl": null' in anonymous_client.get("/api/v1/docs").text


def test_no_hsts_over_plain_http(anonymous_client):
    response = anonymous_client.get("/api/v1/version")

    assert "Strict-Transport-Security" not in response.headers


def test_hsts_is_sent_over_https(app):
    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/v1/version")
        error = client.get("/api/v1/does-not-exist")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert error.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_hsts_max_age_is_configurable(settings, services):
    settings = settings.model_copy(update={"hsts_max_age_seconds": 600})
    with TestClient(create_app(settings, services=services), base_url="https://testserver") as c:
        response = c.get("/healthz")

    assert response.headers["Strict-Transport-Security"] == "max-age=600"


def test_hsts_can_be_turned_off(settings, services):
    settings = settings.model_copy(update={"hsts_max_age_seconds": 0})
    with TestClient(create_app(settings, services=services), base_url="https://testserver") as c:
        response = c.get("/healthz")

    assert "Strict-Transport-Security" not in response.headers
    assert_baseline_headers(response)


def test_api_docs_need_an_openapi_spec():
    with pytest.raises(ValueError, match="openapi_url"):
        install_api_docs(FastAPI(openapi_url=None, docs_url=None), "/docs")


def test_the_api_docs_page_is_the_same_on_every_request(anonymous_client):
    first = anonymous_client.get("/api/v1/docs")
    second = anonymous_client.get("/api/v1/docs")

    assert first.text == second.text
    assert first.headers["Content-Security-Policy"] == second.headers["Content-Security-Policy"]
