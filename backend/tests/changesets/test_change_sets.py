"""The Change Set engine (spec §6.10 "Change Set rules", stories 147, 148).

The engine is driven through ``ChangeSetService`` (proposals come from tools and jobs, not
from HTTP) and the review endpoints. Most rules are shown with an in-memory handler for a
made-up object type, so no object module's details matter; Source Schema enhancements, the
first real handler, are tested at the end against a real Source System.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa

from dawam.modules.audit import AuditService
from dawam.modules.changesets import (
    AppliedChange,
    ChangeSetService,
    ProposedItem,
)
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import (
    WORKSPACE_ACTIONS,
    Action,
    WorkspaceScope,
    WorkspaceService,
    can,
    required_role,
)
from dawam.platform.errors import ApiError
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources.test_enhancements import extracted

NO_SUCH = uuid.UUID(int=0)


class Widgets:
    """An in-memory object type: ``store`` maps an id to its fields."""

    def __init__(self, object_type: str = "widget", action: Action = Action.EDIT_KPI) -> None:
        self.object_type = object_type
        self.action = action
        self.store: dict[uuid.UUID, dict[str, Any]] = {}
        self.applied: list[uuid.UUID] = []

    def make(self, **fields: Any) -> uuid.UUID:
        widget_id = uuid.uuid4()
        self.store[widget_id] = {"name": "w", "color": "red", "size": 1, **fields}
        return widget_id

    def required_action(self, operation: str) -> Action:
        return self.action

    def validate(self, db, workspace_id, operation, object_id, payload) -> None:
        if payload.get("size") == "invalid":
            raise ApiError(422, "invalid_widget", "No such size.")

    def current_values(self, db, workspace_id, object_id, fields: Sequence[str]):
        widget = self.store.get(object_id)
        return None if widget is None else {f: widget.get(f) for f in fields}

    def apply(
        self, db, workspace_id, operation, object_id, payload: Mapping[str, Any], *, at: datetime
    ) -> AppliedChange:
        if payload.get("explode"):
            raise ApiError(422, "invalid_widget", "Cannot apply.")
        widget = self.store[object_id]
        old = {f: widget.get(f) for f in payload}
        widget.update(payload)
        self.applied.append(object_id)
        return AppliedChange("widget", object_id, old, dict(payload))


@pytest.fixture
def widgets(roles: RoleClients) -> Widgets:
    handler = Widgets()
    roles.app.state.change_set_handlers.register(handler)
    return handler


def engine(roles: RoleClients) -> ChangeSetService:
    state = roles.app.state
    clock = state.services.clock
    return ChangeSetService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        handlers=state.change_set_handlers,
        notifications=NotificationService(state.engine, clock=clock),
        clock=clock,
    )


def item(key: str, widget: uuid.UUID, depends_on: Sequence[str] = (), **changes: Any):
    return ProposedItem(
        key=key,
        object_type="widget",
        operation="update",
        object_id=widget,
        payload=changes or {"color": "blue"},
        label=f"widget {key}",
        depends_on=depends_on,
    )


def propose(roles: RoleClients, items, *, scope=None, origin="ai", as_role="editor"):
    roles.client(as_role)  # makes the user a member
    return engine(roles).propose(
        roles.user(as_role),
        roles.workspace_id,
        origin=origin,
        scope=scope or {"kind": "widgets"},
        title="Tidy the widgets",
        items=items,
    )


def review_path(roles: RoleClients, change_set_id) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{change_set_id}"


def decide(roles: RoleClients, change_set_id, verb: str, ids=None, *, as_role="editor"):
    body = {} if ids is None else {"item_ids": [str(i) for i in ids]}
    return roles.client(as_role).post(f"{review_path(roles, change_set_id)}/{verb}", json=body)


def statuses(detail) -> list[str]:
    return [i.status for i in detail.items]


def test_an_editor_accepts_everything_and_it_is_applied_and_audited(
    roles: RoleClients, widgets: Widgets
):
    a, b = widgets.make(), widgets.make()
    proposed = propose(roles, [item("a", a, color="blue"), item("b", b, size=9)])
    assert statuses(proposed) == ["pending", "pending"]
    assert proposed.items[0].base_values == {"color": "red"}
    assert proposed.items[0].required_role == "editor"

    response = decide(roles, proposed.change_set.id, "accept")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["accepted"]) == 2 and body["skipped"] == [] and body["needs_owner"] == []
    assert body["change_set"]["change_set"]["status"] == "applied"
    assert widgets.store[a]["color"] == "blue" and widgets.store[b]["size"] == 9
    [entry] = AuditService(roles.app.state.engine).list(
        roles.workspace_id, entity_type="widget", entity_id=a
    )
    assert (entry.via, entry.actor_id) == ("ai", roles.user("editor").id)
    assert entry.old == {"color": "red"} and entry.new == {"color": "blue"}
    with roles.app.state.engine.connect() as conn:
        linked = conn.execute(
            sa.text("SELECT change_set_id FROM audit_entries WHERE entity_id = :id"), {"id": str(a)}
        ).scalar_one()
    assert linked == proposed.change_set.id


def test_staleness_is_per_changed_field_and_skips_dependents(roles: RoleClients, widgets: Widgets):
    a, b, c, d = (widgets.make() for _ in range(4))
    proposed = propose(
        roles,
        [
            item("a", a, color="blue"),
            item("b", b, color="blue"),
            item("on_b", c, depends_on=["b"], color="green"),
            item("d", d, size=5),
        ],
    )
    widgets.store[a]["name"] = "renamed by a colleague"  # unrelated field: not stale
    widgets.store[b]["color"] = "pink"  # a field the item changes: stale

    body = decide(roles, proposed.change_set.id, "accept").json()

    assert [s["reason"] for s in body["skipped"]] == ["stale", "depends_on_skipped"]
    assert widgets.store[a]["color"] == "blue" and widgets.store[d]["size"] == 5
    assert widgets.store[b]["color"] == "pink" and widgets.store[c]["color"] == "red"
    detail = body["change_set"]
    assert [i["status"] for i in detail["items"]] == ["accepted", "stale", "stale", "accepted"]
    assert detail["change_set"]["status"] == "partially_applied"


def test_an_item_whose_object_is_gone_is_stale(roles: RoleClients, widgets: Widgets):
    a = widgets.make()
    proposed = propose(roles, [item("a", a)])
    del widgets.store[a]

    body = decide(roles, proposed.change_set.id, "accept").json()

    assert body["accepted"] == [] and body["skipped"][0]["reason"] == "stale"
    assert body["change_set"]["change_set"]["status"] == "rejected"


def test_accepting_an_item_accepts_what_it_depends_on(roles: RoleClients, widgets: Widgets):
    a, b, c = (widgets.make() for _ in range(3))
    proposed = propose(
        roles, [item("a", a), item("b", b, depends_on=["a"]), item("c", c, depends_on=["b"])]
    )
    ids = [i.id for i in proposed.items]

    body = decide(roles, proposed.change_set.id, "accept", [ids[1]]).json()

    assert body["accepted"] == [str(ids[0]), str(ids[1])]
    assert [i["status"] for i in body["change_set"]["items"]] == ["accepted", "accepted", "expired"]
    assert body["change_set"]["change_set"]["status"] == "partially_applied"
    assert widgets.store[c]["color"] == "red"


def test_rejecting_an_item_rejects_what_depends_on_it(roles: RoleClients, widgets: Widgets):
    a, b, c = (widgets.make() for _ in range(3))
    proposed = propose(roles, [item("a", a), item("b", b, depends_on=["a"]), item("c", c)])
    ids = [i.id for i in proposed.items]

    response = decide(roles, proposed.change_set.id, "reject", [ids[0]])

    detail = response.json()
    assert [i["status"] for i in detail["items"]] == ["rejected", "rejected", "pending"]
    assert detail["change_set"]["status"] == "pending"  # c still awaits a decision
    final = decide(roles, proposed.change_set.id, "accept").json()
    assert final["accepted"] == [str(ids[2])]
    assert final["change_set"]["change_set"]["status"] == "partially_applied"
    assert widgets.applied == [c]


def test_rejecting_everything_rejects_the_change_set(roles: RoleClients, widgets: Widgets):
    proposed = propose(roles, [item("a", widgets.make()), item("b", widgets.make())])

    detail = decide(roles, proposed.change_set.id, "reject").json()

    assert detail["change_set"]["status"] == "rejected"
    assert {i["status"] for i in detail["items"]} == {"rejected"}
    assert widgets.applied == []


def test_a_closed_change_set_cannot_be_decided_again(roles: RoleClients, widgets: Widgets):
    proposed = propose(roles, [item("a", widgets.make())])
    assert decide(roles, proposed.change_set.id, "accept").status_code == 200

    again = decide(roles, proposed.change_set.id, "accept")
    reject = decide(roles, proposed.change_set.id, "reject")

    assert again.status_code == 409 and again.json()["error"]["code"] == "change_set_closed"
    assert reject.status_code == 409
    assert len(widgets.applied) == 1


def test_a_failing_item_applies_nothing(roles: RoleClients, widgets: Widgets):
    a, b = widgets.make(), widgets.make()
    proposed = propose(roles, [item("a", a, color="blue"), item("b", b, explode=True)])

    response = decide(roles, proposed.change_set.id, "accept")

    assert response.status_code == 422
    # The first item was applied inside the transaction, which rolled back with the second:
    # the Change Set and the audit trail show nothing applied.
    shown = engine(roles).get(roles.user("editor"), roles.workspace_id, proposed.change_set.id)
    assert statuses(shown) == ["pending", "pending"] and shown.change_set.status == "pending"
    assert (
        AuditService(roles.app.state.engine).list(
            roles.workspace_id, entity_type="widget", entity_id=a
        )
        == []
    )


def test_a_newer_change_set_of_the_same_origin_and_scope_supersedes_a_pending_one(
    roles: RoleClients, widgets: Widgets
):
    a = widgets.make()
    first = propose(roles, [item("a", a)], scope={"system": "x"})
    other_scope = propose(roles, [item("a", a)], scope={"system": "y"})
    other_origin = propose(roles, [item("a", a)], scope={"system": "x"}, origin="sync")

    second = propose(roles, [item("a", a, color="green")], scope={"system": "x"})

    service = engine(roles)
    old = service.get(roles.user("editor"), roles.workspace_id, first.change_set.id)
    assert old.change_set.status == "superseded" and statuses(old) == ["expired"]
    for untouched in (other_scope, other_origin, second):
        kept = service.get(roles.user("editor"), roles.workspace_id, untouched.change_set.id)
        assert kept.change_set.status == "pending"
    assert decide(roles, first.change_set.id, "accept").status_code == 409


def test_unselected_items_expire_when_a_change_set_is_closed(roles: RoleClients, widgets: Widgets):
    proposed = propose(roles, [item("a", widgets.make()), item("b", widgets.make())])

    body = decide(roles, proposed.change_set.id, "accept", [proposed.items[0].id]).json()

    assert [i["status"] for i in body["change_set"]["items"]] == ["accepted", "expired"]
    assert body["change_set"]["change_set"]["status"] == "partially_applied"


def test_archiving_a_workspace_rejects_its_pending_change_sets(
    roles: RoleClients, widgets: Widgets
):
    pending = propose(roles, [item("a", widgets.make())])
    applied = propose(roles, [item("a", widgets.make())], scope={"other": 1})
    assert decide(roles, applied.change_set.id, "accept").status_code == 200

    response = roles.client("owner").post(f"/api/v1/workspaces/{roles.workspace_id}/archive")

    assert response.status_code in (200, 204), response.text
    service = engine(roles)
    rejected = service.get(roles.user("owner"), roles.workspace_id, pending.change_set.id)
    assert rejected.change_set.status == "rejected" and statuses(rejected) == ["rejected"]
    kept = service.get(roles.user("owner"), roles.workspace_id, applied.change_set.id)
    assert kept.change_set.status == "applied"
    assert decide(roles, pending.change_set.id, "accept", as_role="owner").status_code == 409


@pytest.mark.parametrize("action", WORKSPACE_ACTIONS, ids=lambda a: a.value)
def test_an_items_required_role_is_derived_from_the_permission_matrix(
    roles: RoleClients, action: Action
):
    workspace_id = roles.workspace_id
    editor_scope = WorkspaceScope(workspace_id, roles.user("editor").id, "editor", archived=False)
    owner_scope = WorkspaceScope(workspace_id, roles.user("owner").id, "owner", archived=False)
    if not can(roles.user("owner"), action, owner_scope):
        pytest.skip("not performed by Workspace roles on an active Workspace")

    expected = "editor" if can(roles.user("editor"), action, editor_scope) else "owner"

    assert required_role(action) == expected


@pytest.mark.parametrize(
    ("action", "needs"),
    [
        (Action.EDIT_KPI, "editor"),
        (Action.CHANGE_SYSTEM_CODE, "owner"),
        (Action.ENABLE_TOP_N, "owner"),
    ],
)
def test_owner_only_items_stay_needs_owner_when_an_editor_accepts(
    roles: RoleClients, action: Action, needs: str
):
    handler = Widgets("gadget", action)
    roles.app.state.change_set_handlers.register(handler)
    free_handler = Widgets("widget")
    roles.app.state.change_set_handlers.register(free_handler)
    gadget, plain, dependent = handler.make(), free_handler.make(), free_handler.make()
    items = [
        ProposedItem("g", "gadget", "update", {"color": "blue"}, object_id=gadget, label="gadget"),
        item("w", plain, size=2),
        item("dep", dependent, depends_on=["g"], size=3),
    ]
    proposed = propose(roles, items)
    assert proposed.items[0].required_role == needs

    body = decide(roles, proposed.change_set.id, "accept").json()
    shown = [i["status"] for i in body["change_set"]["items"]]

    if needs == "editor":
        assert shown == ["accepted"] * 3 and body["needs_owner"] == []
        return
    assert shown == ["needs_owner", "accepted", "pending"]
    assert body["needs_owner"] == [str(proposed.items[0].id), str(proposed.items[2].id)]
    assert body["change_set"]["change_set"]["status"] == "pending"  # an owner still decides
    assert handler.store[gadget]["color"] == "red" and free_handler.store[plain]["size"] == 2
    unread = NotificationService(
        roles.app.state.engine, clock=roles.app.state.services.clock
    ).unread(roles.user("owner"))
    [note] = [n for n in unread.items if n.kind == "needs_owner"]
    assert (note.ref_type, note.ref_id) == ("change_set", proposed.change_set.id)

    owner = decide(roles, proposed.change_set.id, "accept", as_role="owner").json()

    assert [i["status"] for i in owner["change_set"]["items"]] == ["accepted"] * 3
    assert owner["change_set"]["change_set"]["status"] == "applied"
    assert handler.store[gadget]["color"] == "blue"


def test_a_viewer_cannot_decide_and_a_non_member_cannot_see(roles: RoleClients, widgets: Widgets):
    proposed = propose(roles, [item("a", widgets.make())])

    assert decide(roles, proposed.change_set.id, "accept", as_role="viewer").status_code == 403
    assert decide(roles, proposed.change_set.id, "reject", as_role="viewer").status_code == 403
    assert roles.client("viewer").get(review_path(roles, proposed.change_set.id)).status_code == 200
    assert (
        roles.client("non_member").get(review_path(roles, proposed.change_set.id)).status_code
        == 404
    )
    with pytest.raises(ApiError) as denied:
        propose(roles, [item("a", widgets.make())], as_role="viewer")
    assert denied.value.status_code == 403


def test_malformed_proposals_are_refused(roles: RoleClients, widgets: Widgets):
    a = widgets.make()

    def code(*items: ProposedItem) -> str:
        with pytest.raises(ApiError) as caught:
            propose(roles, list(items))
        assert caught.value.status_code == 422
        return caught.value.code

    assert code() == "invalid_change_set"
    assert code(item("a", a), item("a", a)) == "invalid_change_set"
    assert code(item("a", a, depends_on=["later"]), item("later", a)) == "invalid_change_set"
    assert code(item("a", NO_SUCH)) == "invalid_change_set"
    assert code(item("a", a, size="invalid")) == "invalid_widget"
    assert code(ProposedItem("a", "nonsense", "update", {}, object_id=a)) == "unknown_object_type"
    assert code(ProposedItem("a", "widget", "update", {"size": 1})) == "invalid_change_set"
    assert (
        code(ProposedItem("a", "widget", "bogus", {"size": 1}, object_id=a)) == "invalid_change_set"
    )


def test_a_change_set_lists_newest_first_with_item_counts(
    roles: RoleClients, widgets: Widgets, clock
):
    from datetime import timedelta

    first = propose(roles, [item("a", widgets.make()), item("b", widgets.make())], scope={"n": 1})
    clock.advance(timedelta(minutes=1))
    second = propose(roles, [item("a", widgets.make())], scope={"n": 2})
    decide(roles, second.change_set.id, "reject")

    listing = roles.client("viewer").get(f"/api/v1/workspaces/{roles.workspace_id}/change-sets")
    pending = roles.client("viewer").get(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets", params={"status": "pending"}
    )

    assert [c["id"] for c in listing.json()["items"]] == [
        str(second.change_set.id),
        str(first.change_set.id),
    ]
    assert listing.json()["items"][1]["item_counts"] == {"pending": 2}
    assert [c["id"] for c in pending.json()["items"]] == [str(first.change_set.id)]
    assert roles.client("viewer").get(review_path(roles, uuid.uuid4())).status_code == 404


# -- Source Schema enhancements, the first real object handler --------------------------


def test_source_enhancements_are_proposed_and_applied_per_field(
    roles: RoleClients, sample_source: SampleSource
):
    system, table, column = extracted(roles, sample_source)
    editor = roles.client("editor")
    proposed = propose(
        roles,
        [
            ProposedItem(
                "t",
                "source_table",
                "update",
                {"description": "Bank customers", "classification": "master"},
                object_id=uuid.UUID(table["id"]),
                label="core.customers",
            ),
            ProposedItem(
                "c",
                "source_column",
                "update",
                {"tags": ["key"], "description": "Surrogate key"},
                object_id=uuid.UUID(column["id"]),
                label="core.customers.id",
            ),
        ],
        scope={"source_system": system},
    )
    # A colleague edits a field the table item does NOT change, and one the column item does.
    assert (
        editor.patch(
            f"{system}/tables/{table['id']}", json={"version": table["version"], "tags": ["pii"]}
        ).status_code
        == 200
    )
    assert (
        editor.patch(
            f"{system}/tables/{table['id']}/columns/{column['id']}",
            json={"version": column["version"], "description": "Mine"},
        ).status_code
        == 200
    )

    body = decide(roles, proposed.change_set.id, "accept").json()

    assert [s["reason"] for s in body["skipped"]] == ["stale"]
    assert body["skipped"][0]["item_id"] == str(proposed.items[1].id)
    shown = {t["id"]: t for t in roles.client("viewer").get(f"{system}/schema").json()["tables"]}
    seen = shown[table["id"]]
    assert (seen["description"], seen["classification"], seen["tags"]) == (
        "Bank customers",
        "master",
        ["pii"],
    )
    [seen_column] = [c for c in seen["columns"] if c["id"] == column["id"]]
    assert seen_column["description"] == "Mine" and seen_column["tags"] == []
    entries = AuditService(roles.app.state.engine).list(
        roles.workspace_id, entity_type="source_table", entity_id=table["id"]
    )
    [applied] = [e for e in entries if e.via == "ai"]
    assert applied.new == {"description": "Bank customers", "classification": "master"}


def test_source_enhancement_items_are_validated_when_proposed(
    roles: RoleClients, sample_source: SampleSource
):
    _, table, _ = extracted(roles, sample_source)

    def refused(payload: dict) -> str:
        with pytest.raises(ApiError) as caught:
            propose(
                roles,
                [
                    ProposedItem(
                        "t", "source_table", "update", payload, object_id=uuid.UUID(table["id"])
                    )
                ],
            )
        return caught.value.code

    assert refused({"classification": "nonsense"}) == "invalid_enhancement"
    assert refused({"name": "x"}) == "invalid_change_set"
    assert refused({}) == "invalid_change_set"
    with pytest.raises(ApiError) as deleting:
        propose(
            roles,
            [ProposedItem("t", "source_table", "delete", {}, object_id=uuid.UUID(table["id"]))],
        )
    assert deleting.value.status_code == 422


def test_a_needs_owner_item_survives_a_later_subset_decision(roles: RoleClients):
    gadgets, plain = Widgets("gadget", Action.CHANGE_SYSTEM_CODE), Widgets("widget")
    for handler in (gadgets, plain):
        roles.app.state.change_set_handlers.register(handler)
    gadget = gadgets.make()
    proposed = propose(
        roles,
        [
            ProposedItem("g", "gadget", "update", {"color": "blue"}, object_id=gadget),
            item("w", plain.make()),
        ],
    )
    first = decide(roles, proposed.change_set.id, "accept", [proposed.items[0].id]).json()
    assert [i["status"] for i in first["change_set"]["items"]] == ["needs_owner", "expired"]

    again = decide(roles, proposed.change_set.id, "accept", []).json()

    assert again["change_set"]["items"][0]["status"] == "needs_owner"
    assert again["change_set"]["change_set"]["status"] == "pending"
    owner = decide(roles, proposed.change_set.id, "accept", as_role="owner").json()
    assert owner["accepted"] == [str(proposed.items[0].id)]
    assert gadgets.store[gadget]["color"] == "blue"


def test_an_item_that_changes_nothing_leaves_no_audit_entry(roles: RoleClients, widgets: Widgets):
    a = widgets.make()
    proposed = propose(roles, [item("a", a, color="red")])  # already red

    body = decide(roles, proposed.change_set.id, "accept").json()

    assert body["accepted"] == [str(proposed.items[0].id)]
    audit = AuditService(roles.app.state.engine)
    assert audit.list(roles.workspace_id, entity_type="widget", entity_id=a) == []


def test_concurrent_proposals_of_one_scope_leave_one_pending_set(
    roles: RoleClients, widgets: Widgets
):
    from concurrent.futures import ThreadPoolExecutor

    roles.client("editor")
    a = widgets.make()

    def run(_: int):
        return propose(roles, [item("a", a)], scope={"same": True})

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(run, range(4)))

    page = engine(roles).list(roles.user("editor"), roles.workspace_id, status="pending")
    assert len(page.items) == 1


def test_change_sets_of_a_private_conversation_are_hidden_from_other_members(
    roles: RoleClients, widgets: Widgets
):
    base = f"/api/v1/workspaces/{roles.workspace_id}"
    owner = roles.client("owner")
    created = owner.post(f"{base}/assistant/conversations", json={})
    conversation = created.json()["id"]
    roles.client("editor")
    service = engine(roles)
    proposed = service.propose(
        roles.user("owner"),
        roles.workspace_id,
        origin="ai",
        scope={"k": 1},
        title="Private",
        items=[item("a", widgets.make())],
        conversation_id=uuid.UUID(conversation),
    )
    plain = propose(roles, [item("a", widgets.make())], scope={"k": 2})
    path = f"{base}/change-sets"

    def ids(role):
        return {c["id"] for c in roles.client(role).get(path).json()["items"]}

    assert ids("owner") == {str(proposed.change_set.id), str(plain.change_set.id)}
    assert ids("editor") == {str(plain.change_set.id)}
    assert roles.client("editor").get(f"{path}/{proposed.change_set.id}").status_code == 404
    assert roles.client("owner").get(f"{path}/{proposed.change_set.id}").status_code == 200

    shared = owner.patch(
        f"{base}/assistant/conversations/{conversation}", json={"shared_with_workspace": True}
    )
    assert shared.status_code == 200
    assert str(proposed.change_set.id) in ids("editor")
    assert roles.client("editor").get(f"{path}/{proposed.change_set.id}").status_code == 200
