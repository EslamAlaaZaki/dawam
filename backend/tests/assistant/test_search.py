"""Searching past conversations and job summaries through the HTTP API (AI enhancements §7).

Messages are written straight into the tables (no model is called); the signed-in clients
then search as each role, so sharing and membership are what decide the results.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from tests.roles import RoleClients


def base(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/assistant/conversations"


def start(roles: RoleClients, role: str, *, shared: bool = False, title: str | None = None) -> str:
    client = roles.client(role)  # type: ignore[arg-type]
    created = client.post(base(roles), json={"title": title} if title else {})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    if shared:
        assert client.patch(f"{base(roles)}/{cid}", json={"shared_with_workspace": True}).is_success
    return cid


def write(roles: RoleClients, cid: str, content: str, *, role: str = "assistant", at: int = 0):
    with roles.app.state.engine.begin() as conn:
        position = conn.execute(
            sa.text(
                "SELECT coalesce(max(position), 0) + 1 FROM assistant_messages "
                "WHERE conversation_id = :c"
            ),
            {"c": cid},
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO assistant_messages (id, conversation_id, position, role, content, "
                "created_at) VALUES (:id, :c, :p, :r, :content, :now)"
            ),
            {
                "id": uuid.uuid4(),
                "c": cid,
                "p": position,
                "r": role,
                "content": content,
                "now": datetime.now(UTC) + timedelta(seconds=at),
            },
        )


def search(roles: RoleClients, role: str, q: str):
    return roles.client(role).get(f"{base(roles)}/search", params={"q": q})  # type: ignore[arg-type]


def test_finds_own_private_conversation_by_message(roles: RoleClients):
    cid = start(roles, "owner")
    write(roles, cid, "The churn_flag column lives in dim_customer.", role="user")

    response = search(roles, "owner", "CHURN_flag")

    assert response.status_code == 200, response.text
    [hit] = response.json()["items"]
    assert hit["kind"] == "message"
    assert hit["conversation_id"] == cid
    assert hit["mine"] is True
    assert "churn_flag" in hit["snippet"]


def test_other_members_private_conversations_never_appear(roles: RoleClients):
    private = start(roles, "owner", title="Secret budget plan")
    write(roles, private, "Secret budget numbers", role="user")
    write(roles, private, "Job finished: secret budget summary")

    for role in ("editor", "viewer"):
        assert search(roles, role, "secret").json()["items"] == [], role


def test_shared_conversation_and_job_summary_are_found_by_members(roles: RoleClients):
    cid = start(roles, "owner", shared=True, title="Quarterly lineage review")
    write(roles, cid, "Job finished: mapped the orders lineage end to end.")
    write(roles, cid, "Which orders tables feed the lineage?", role="user")

    for role in ("viewer", "editor", "owner"):
        items = search(roles, role, "lineage").json()["items"]
        kinds = {h["kind"] for h in items}
        assert kinds == {"conversation", "message", "job_summary"}, role
        assert {h["conversation_id"] for h in items} == {cid}
        assert all(h["shared_with_workspace"] for h in items)

    viewer_hits = search(roles, "viewer", "lineage").json()["items"]
    assert all(h["mine"] is False for h in viewer_hits)


def test_unsharing_hides_the_conversation_from_search(roles: RoleClients):
    cid = start(roles, "owner", shared=True)
    write(roles, cid, "Findings about the ledger tables", role="user")
    assert len(search(roles, "viewer", "ledger").json()["items"]) == 1

    owner = roles.client("owner")
    assert owner.patch(f"{base(roles)}/{cid}", json={"shared_with_workspace": False}).is_success

    assert search(roles, "viewer", "ledger").json()["items"] == []
    assert len(search(roles, "owner", "ledger").json()["items"]) == 1


def test_non_members_get_404_and_anonymous_401(roles: RoleClients):
    cid = start(roles, "owner", shared=True)
    write(roles, cid, "ledger notes", role="user")

    assert search(roles, "non_member", "ledger").status_code == 404
    assert search(roles, "admin", "ledger").status_code == 404
    assert search(roles, "anonymous", "ledger").status_code == 401


def test_search_stays_inside_the_workspace(roles: RoleClients):
    cid = start(roles, "owner", shared=True)
    write(roles, cid, "ledger notes", role="user")
    other = roles.client("owner").post(
        "/api/v1/workspaces", json={"name": "Other", "description": "", "domain": "Testing"}
    )
    assert other.status_code == 201, other.text

    response = roles.client("owner").get(
        f"/api/v1/workspaces/{other.json()['id']}/assistant/conversations/search",
        params={"q": "ledger"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_wildcards_are_literal_and_blank_queries_match_nothing(roles: RoleClients):
    cid = start(roles, "owner")
    write(roles, cid, "100% of rows", role="user")
    write(roles, cid, "plain text", role="user")

    assert len(search(roles, "owner", "100%").json()["items"]) == 1
    assert search(roles, "owner", "%").json()["items"][0]["snippet"] == "100% of rows"
    assert search(roles, "owner", "   ").json()["items"] == []
