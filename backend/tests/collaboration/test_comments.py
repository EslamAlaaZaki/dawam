"""Comments and @mentions (spec §4.3 "Comment", stories 107, 125, 126)."""

from __future__ import annotations

import uuid

from tests.roles import RoleClients

OBJECT_ID = str(uuid.uuid4())


def comments_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/comments"


def comment(roles: RoleClients, *, as_role="viewer", **fields):
    body = {"object_type": "kpi", "object_id": OBJECT_ID, "body": "Is this net of fees?", **fields}
    return roles.client(as_role).post(comments_path(roles), json=body)


def threads(roles: RoleClients, *, as_role="viewer", object_type="kpi", object_id=OBJECT_ID):
    response = roles.client(as_role).get(
        comments_path(roles), params={"object_type": object_type, "object_id": object_id}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def unread(roles: RoleClients, role: str) -> list[dict]:
    return roles.client(role).get("/api/v1/notifications").json()["items"]


def member(roles: RoleClients, role: str):
    """The role's user; the Workspace membership exists once its client does."""
    roles.client(role)
    return roles.user(role)


def error_code(response) -> str:
    return response.json()["error"]["code"]


def test_a_viewer_comments_and_the_thread_lists_it(roles: RoleClients):
    response = comment(roles, as_role="viewer")

    assert response.status_code == 201
    created = response.json()
    assert created["body"] == "Is this net of fees?"
    assert created["author"]["display_name"] == "Viewer"
    assert created["parent_id"] is None and created["resolved_at"] is None
    [thread] = threads(roles, as_role="editor")
    assert thread["id"] == created["id"] and thread["replies"] == []


def test_replies_join_their_thread_and_stay_on_its_object(roles: RoleClients):
    root = comment(roles).json()

    reply = comment(roles, as_role="editor", parent_id=root["id"], body="Yes, net.")

    assert reply.status_code == 201
    [thread] = threads(roles)
    assert [r["body"] for r in thread["replies"]] == ["Yes, net."]
    # A reply to a reply, or to another object's thread, is refused.
    nested = comment(roles, parent_id=reply.json()["id"])
    assert (nested.status_code, error_code(nested)) == (422, "invalid_comment")
    other = comment(roles, parent_id=root["id"], object_id=str(uuid.uuid4()))
    assert (other.status_code, error_code(other)) == (422, "invalid_comment")


def test_threads_are_per_object_and_type(roles: RoleClients):
    comment(roles)
    comment(roles, object_type="dw_column")

    assert len(threads(roles)) == 1
    assert threads(roles, object_type="mapping") == []


def test_any_member_resolves_and_reopens_a_thread(roles: RoleClients):
    root = comment(roles).json()
    url = f"{comments_path(roles)}/{root['id']}"

    resolved = roles.client("editor").post(f"{url}/resolve")

    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None
    assert resolved.json()["resolved_by"]["display_name"] == "Editor"
    assert threads(roles)[0]["comment"]["resolved_at"] is not None
    reopened = roles.client("viewer").post(f"{url}/reopen")
    assert reopened.status_code == 200 and reopened.json()["resolved_at"] is None


def test_only_a_thread_root_resolves(roles: RoleClients):
    root = comment(roles).json()
    reply = comment(roles, parent_id=root["id"]).json()

    response = roles.client("editor").post(f"{comments_path(roles)}/{reply['id']}/resolve")

    assert (response.status_code, error_code(response)) == (422, "invalid_comment")
    missing = roles.client("editor").post(f"{comments_path(roles)}/{uuid.uuid4()}/resolve")
    assert missing.status_code == 404


def test_mentioning_members_notifies_them_but_not_the_author(roles: RoleClients):
    editor, viewer, owner = (str(member(roles, r).id) for r in ("editor", "viewer", "owner"))

    response = comment(
        roles, as_role="viewer", body="@Editor @Owner please look", mentions=[editor, owner, viewer]
    )

    assert response.status_code == 201
    assert sorted(response.json()["mentions"]) == sorted([editor, owner, viewer])
    [note] = unread(roles, "editor")
    assert note["kind"] == "mention"
    assert note["ref_type"] == "comment" and note["ref_id"] == response.json()["id"]
    assert "Viewer" in note["message"]
    assert len(unread(roles, "owner")) == 1
    assert unread(roles, "viewer") == []


def test_mentioning_a_non_member_is_refused_and_notifies_nobody(roles: RoleClients):
    editor = str(member(roles, "editor").id)
    outsider = str(roles.user("non_member").id)

    response = comment(roles, mentions=[editor, outsider])

    assert (response.status_code, error_code(response)) == (422, "invalid_mention")
    assert unread(roles, "editor") == []
    assert threads(roles) == []


def test_bodies_and_object_types_are_validated(roles: RoleClients):
    assert comment(roles, body="   ").status_code == 422
    assert comment(roles, object_type="workspace").status_code == 422
    assert comment(roles, body="x" * 5000).status_code == 422


def test_non_members_cannot_comment_or_read(roles: RoleClients):
    assert comment(roles, as_role="non_member").status_code == 404
    response = roles.client("non_member").get(
        comments_path(roles), params={"object_type": "kpi", "object_id": OBJECT_ID}
    )
    assert response.status_code == 404


def test_an_archived_workspace_takes_no_new_comments(roles: RoleClients):
    root = comment(roles).json()
    archived = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/archive")
    assert archived.status_code in (200, 204)

    response = comment(roles, parent_id=root["id"])

    assert response.status_code == 409
    assert len(threads(roles)) == 1
