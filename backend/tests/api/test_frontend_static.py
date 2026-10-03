"""When DAWAM_FRONTEND_DIST is set, the app also serves the built frontend at /."""

import pytest
from fastapi.testclient import TestClient

from dawam.app import create_app


@pytest.fixture
def frontend_client(tmp_path, settings, services):
    (tmp_path / "index.html").write_text("<!doctype html><title>DAWAM</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    settings = settings.model_copy(update={"frontend_dist": tmp_path})
    with TestClient(create_app(settings, services=services)) as client:
        yield client


def test_index_page_is_served_at_the_root(frontend_client):
    response = frontend_client.get("/")

    assert response.status_code == 200
    assert "<title>DAWAM</title>" in response.text


def test_assets_are_served(frontend_client):
    assert frontend_client.get("/assets/app.js").text == "console.log('hi')"


def test_the_api_still_wins_over_the_frontend(frontend_client):
    assert frontend_client.get("/api/v1/version").json()["name"] == "DAWAM"
    assert frontend_client.get("/healthz").json() == {"status": "ok"}
    assert frontend_client.get("/api/v1/nope").json()["error"]["code"] == "not_found"
