import dawam


def test_healthz_reports_the_process_is_up(anonymous_client):
    response = anonymous_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_version_is_served_under_api_v1(anonymous_client):
    response = anonymous_client.get("/api/v1/version")

    assert response.status_code == 200
    assert response.json() == {"name": "DAWAM", "version": dawam.__version__}


def test_openapi_spec_lives_under_api_v1(anonymous_client):
    spec = anonymous_client.get("/api/v1/openapi.json").json()

    assert spec["info"]["version"] == dawam.__version__
    assert spec["paths"]
    assert all(path.startswith("/api/v1/") for path in spec["paths"])
