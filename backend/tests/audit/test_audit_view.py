"""The audit view (spec §6.10, story 127): filterable, newest first, readable by every
member, with Connection entries redacted for everyone but owners."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from tests.roles import RoleClients


def audit_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/audit"


def trail(roles: RoleClients, as_role="owner", **params) -> dict:
    response = roles.client(as_role).get(audit_path(roles), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def write(roles: RoleClients, app, clock, **fields) -> None:
    """Records one entry a minute after the last, as the owner unless told otherwise."""
    clock.advance(timedelta(minutes=1))
    fields.setdefault("actor_id", roles.user("owner").id)
    fields.setdefault("old", None)
    fields.setdefault("new", {"name": "x"})
    with Session(app.state.engine) as db, db.begin():
        record_audit(db, workspace_id=roles.workspace_id, at=clock(), **fields)


def ids(page: dict) -> list[str]:
    return [i["entity_id"] for i in page["items"]]


def iso(moment) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def test_the_trail_is_newest_first_with_actor_channel_and_values(roles, app, clock):
    write(roles, app, clock, entity_type="kpi", entity_id="k1", new={"unit": "%"})
    write(
        roles,
        app,
        clock,
        entity_type="kpi",
        entity_id="k1",
        old={"unit": "%"},
        new={"unit": "bps"},
        via="ai",
        actor_id=roles.user("editor").id,
    )

    page = trail(roles, as_role="viewer")

    newest, oldest = page["items"]
    assert newest["old"] == {"unit": "%"} and newest["new"] == {"unit": "bps"}
    assert newest["via"] == "ai"
    assert newest["actor"]["user_id"] == str(roles.user("editor").id)
    assert newest["actor"]["display_name"] == "Editor"
    assert oldest["old"] is None
    assert page["next_cursor"] is None


def test_it_filters_by_entity_object_actor_channel_and_date(roles, app, clock):
    write(roles, app, clock, entity_type="kpi", entity_id="k1")
    write(roles, app, clock, entity_type="member", entity_id="m1", via="import")
    clock.advance(timedelta(seconds=30))
    cutoff = clock()
    write(roles, app, clock, entity_type="kpi", entity_id="k2", actor_id=roles.user("editor").id)

    assert ids(trail(roles, entity_type="kpi")) == ["k2", "k1"]
    assert ids(trail(roles, entity_type="kpi", entity_id="k1")) == ["k1"]
    assert ids(trail(roles, via="import")) == ["m1"]
    assert ids(trail(roles, actor_id=str(roles.user("editor").id))) == ["k2"]
    assert ids(trail(roles, since=iso(cutoff))) == ["k2"]
    assert ids(trail(roles, until=iso(cutoff))) == ["m1", "k1"]


def test_it_is_paged_and_refuses_unknown_channels_and_cursors(roles, app, clock):
    for n in range(3):
        write(roles, app, clock, entity_type="kpi", entity_id=f"k{n}")

    first = trail(roles, limit=2)
    second = trail(roles, limit=2, cursor=first["next_cursor"])

    assert ids(first) + ids(second) == ["k2", "k1", "k0"]
    assert second["next_cursor"] is None
    owner = roles.client("owner")
    assert owner.get(audit_path(roles), params={"via": "magic"}).status_code == 422
    assert owner.get(audit_path(roles), params={"cursor": "garbage"}).status_code == 422


def test_non_members_cannot_read_it(roles):
    assert roles.client("non_member").get(audit_path(roles)).status_code == 404


def test_connection_entries_never_show_secrets_and_hide_host_and_user_from_non_owners(
    roles, app, clock
):
    write(
        roles,
        app,
        clock,
        entity_type="connection",
        entity_id="c1",
        old={"host": "old.db", "username": "etl", "password": "hunter2", "port": 5432},
        new={"host": "new.db", "username": "etl2", "secret_encrypted": "ciphertext", "port": 5433},
    )

    as_owner = trail(roles, as_role="owner")["items"][0]
    for role in ("editor", "viewer"):
        redacted = trail(roles, as_role=role)["items"][0]
        assert redacted["old"] == {"port": 5432}
        assert redacted["new"] == {"port": 5433}

    assert as_owner["old"] == {"host": "old.db", "username": "etl", "port": 5432}
    assert as_owner["new"] == {"host": "new.db", "username": "etl2", "port": 5433}
    assert "hunter2" not in str(as_owner) and "ciphertext" not in str(as_owner)


def test_other_entities_are_shown_whole_and_there_is_no_restore_route(roles, app, clock):
    write(roles, app, clock, entity_type="kpi", entity_id="k1", new={"host": "kept"})

    assert trail(roles, as_role="viewer")["items"][0]["new"] == {"host": "kept"}
    item = trail(roles)["items"][0]
    restore = roles.client("owner").post(f"{audit_path(roles)}/{item['id']}/restore")
    assert restore.status_code in (404, 405)
