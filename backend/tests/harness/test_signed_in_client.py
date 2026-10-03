"""The S1 harness's ``signed_in_client`` is a real session obtained through the API."""

from fastapi.testclient import TestClient

from tests.helpers import sign_out


def test_signed_in_client_is_signed_in_as_the_signed_in_user(
    signed_in_client: TestClient, signed_in_user
):
    response = signed_in_client.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(signed_in_user.id),
        "email": "ada@example.com",
        "display_name": "Ada Lovelace",
        "system_role": "user",
    }


def test_signed_in_client_sends_the_csrf_token_on_state_changing_requests(
    signed_in_client: TestClient,
):
    response = signed_in_client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert signed_in_client.get("/api/v1/me").status_code == 401


def test_anonymous_and_signed_in_clients_are_separate(
    anonymous_client: TestClient, signed_in_client: TestClient
):
    assert anonymous_client.get("/api/v1/me").status_code == 401
    assert signed_in_client.get("/api/v1/me").status_code == 200

    assert sign_out(anonymous_client).status_code == 204
    assert signed_in_client.get("/api/v1/me").status_code == 200
