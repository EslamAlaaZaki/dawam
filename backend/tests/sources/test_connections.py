"""A Source System's live PostgreSQL Connection (spec stories 40-43, §6.3, §4.3).

Behaviour is driven through the HTTP API against the seeded sample source database.
Two deliberate storage-property checks read the module's own ``connections`` table: the
password is stored sealed, and never appears in a response.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa

from dawam.platform.crypto import SecretBox
from tests.roles import RoleClients
from tests.sample_source import SampleSource


def system(roles: RoleClients) -> str:
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "Core", "code": "cbs"}
    )
    assert response.status_code == 201, response.text
    return f"/api/v1/workspaces/{roles.workspace_id}/systems/{response.json()['id']}/connection"


def error_code(response) -> str:
    return response.json()["error"]["code"]


def test_an_owner_saves_a_connection_and_the_password_is_never_returned(
    roles: RoleClients, sample_source: SampleSource, app
):
    path = system(roles)
    owner = roles.client("owner")
    assert owner.get(path).status_code == 404

    response = owner.put(path, json=sample_source.connection_body())

    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["has_password"] is True
    assert "password" not in saved and "secret" not in json.dumps(saved).lower()
    assert "reader-secret" not in response.text
    assert saved["host"] == sample_source.host
    assert saved["allowed_schemas"] == ["core", "crm"]
    assert saved["can_write"] is None and saved["last_tested_at"] is None
    opened = owner.get(path)
    assert opened.status_code == 200
    assert opened.json() == saved
    assert "reader-secret" not in opened.text

    with app.state.engine.connect() as conn:
        sealed = conn.scalar(sa.text("SELECT secret_encrypted FROM connections"))
    assert "reader-secret" not in sealed
    box = SecretBox(app.state.settings.encryption_key.get_secret_value())
    assert box.decrypt(sealed, context="connection.password") == "reader-secret"


def test_saving_again_replaces_the_connection_and_keeps_the_password_when_left_out(
    roles: RoleClients, sample_source: SampleSource, app
):
    path = system(roles)
    owner = roles.client("owner")
    owner.put(path, json=sample_source.connection_body())

    body = sample_source.connection_body(allowed_schemas=["core"])
    del body["password"]
    response = owner.put(path, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["allowed_schemas"] == ["core"]
    assert response.json()["has_password"] is True
    tested = owner.post(f"{path}/test", json=body)
    assert tested.json()["ok"] is True, "the stored password is still used"

    cleared = owner.put(path, json={**body, "password": ""})
    assert cleared.json()["has_password"] is False


def test_there_is_one_connection_per_source_system(
    roles: RoleClients, sample_source: SampleSource, app
):
    path = system(roles)
    owner = roles.client("owner")
    owner.put(path, json=sample_source.connection_body())
    owner.put(path, json=sample_source.connection_body(database="other"))

    with app.state.engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT count(*) FROM connections")) == 1


def test_a_test_before_saving_reports_success_and_the_schemas_to_pick_from(
    roles: RoleClients, sample_source: SampleSource
):
    path = system(roles)

    response = roles.client("owner").post(f"{path}/test", json=sample_source.connection_body())

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is True
    assert result["error"] is None
    assert result["server_version"].startswith("16")
    assert {"core", "crm", "restricted"} <= set(result["available_schemas"])
    assert "pg_catalog" not in result["available_schemas"]
    assert result["missing_schemas"] == []
    assert result["can_write"] is False


def test_a_test_reports_a_missing_allowed_schema(roles: RoleClients, sample_source: SampleSource):
    path = system(roles)

    result = (
        roles.client("owner")
        .post(f"{path}/test", json=sample_source.connection_body(allowed_schemas=["core", "nope"]))
        .json()
    )

    assert result["ok"] is True
    assert result["missing_schemas"] == ["nope"]


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"password": "wrong-password"}, "authentication_failed"),
        ({"database": "no_such_database"}, "database_not_found"),
        ({"port": 1}, "connection_failed"),
    ],
)
def test_a_failed_test_gives_a_clear_error_that_leaks_nothing(
    roles: RoleClients, sample_source: SampleSource, override, code, caplog
):
    path = system(roles)
    body = sample_source.connection_body(**override)

    with caplog.at_level(logging.DEBUG):
        response = roles.client("owner").post(f"{path}/test", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert result["error_code"] == code
    assert result["error"]
    for secret in (str(body["password"]), sample_source.host, str(body["username"])):
        assert secret not in response.text
        assert secret not in caplog.text


def test_a_user_that_can_write_is_flagged(roles: RoleClients, sample_source: SampleSource):
    path = system(roles)
    owner = roles.client("owner")

    reader = owner.post(f"{path}/test", json=sample_source.connection_body()).json()
    writer = owner.post(f"{path}/test", json=sample_source.connection_body(user="writer")).json()
    admin = owner.post(f"{path}/test", json=sample_source.connection_body(user="admin")).json()

    assert (reader["can_write"], writer["can_write"], admin["can_write"]) == (False, True, True)


def test_testing_the_saved_settings_records_the_result_on_the_connection(
    roles: RoleClients, sample_source: SampleSource, clock
):
    path = system(roles)
    owner = roles.client("owner")
    body = sample_source.connection_body(user="writer")
    owner.put(path, json=body)
    del body["password"]

    owner.post(f"{path}/test", json=body)

    saved = owner.get(path).json()
    assert saved["can_write"] is True
    assert saved["last_tested_at"] == clock().isoformat().replace("+00:00", "Z")

    owner.put(path, json={**body, "host": "127.0.0.1"})
    again = owner.get(path).json()
    assert again["can_write"] is None and again["last_tested_at"] is None


@pytest.mark.parametrize(
    "override",
    [
        {"host": "  "},
        {"port": 0},
        {"port": 70000},
        {"database": ""},
        {"username": ""},
        {"allowed_schemas": []},
        {"allowed_schemas": [" "]},
        {"options": {"search_path": "x"}},
        {"options": {"statement_timeout_seconds": 100000}},
        {"options": {"sslmode": "sometimes"}},
    ],
)
def test_invalid_settings_are_refused(roles: RoleClients, sample_source: SampleSource, override):
    path = system(roles)

    response = roles.client("owner").put(path, json=sample_source.connection_body(**override))

    assert response.status_code == 422
    assert error_code(response) in {"invalid_connection", "validation_error"}


def test_only_the_allowed_options_are_kept(roles: RoleClients, sample_source: SampleSource):
    path = system(roles)
    options = {"sslmode": "prefer", "connect_timeout": 5, "statement_timeout_seconds": 10}

    response = roles.client("owner").put(path, json=sample_source.connection_body(options=options))

    assert response.json()["options"] == options


def test_a_system_of_another_workspace_has_no_connection_here(
    roles: RoleClients, sample_source: SampleSource
):
    path = system(roles)
    other = path.replace(str(roles.workspace_id), str(uuid.uuid4()))

    assert roles.client("owner").get(other).status_code == 404
    assert roles.client("owner").get(path.replace("/connection", "x/connection")).status_code in (
        404,
        422,
    )


def test_saving_is_recorded_in_the_activity_feed_without_host_or_secrets(
    roles: RoleClients, sample_source: SampleSource, clock
):
    path = system(roles)
    owner = roles.client("owner")
    owner.put(path, json=sample_source.connection_body())
    clock.advance(timedelta(minutes=1))
    owner.put(path, json=sample_source.connection_body(allowed_schemas=["core"]))

    feed = owner.get(f"/api/v1/workspaces/{roles.workspace_id}/activity")

    verbs = [i["verb"] for i in feed.json()["items"] if i["verb"].startswith("connection.")]
    assert verbs == ["connection.updated", "connection.created"]
    for leaked in (sample_source.host, "reader-secret", "dawam_reader", sample_source.database):
        assert leaked not in feed.text


def test_an_editor_cannot_see_or_change_a_connection(
    roles: RoleClients, sample_source: SampleSource
):
    path = system(roles)
    roles.client("owner").put(path, json=sample_source.connection_body())

    for role in ("editor", "viewer"):
        client = roles.client(role)
        assert client.get(path).status_code == 403
        assert client.put(path, json=sample_source.connection_body()).status_code == 403
        assert client.post(f"{path}/test", json=sample_source.connection_body()).status_code == 403
