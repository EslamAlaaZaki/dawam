"""Manually added relationships and deleting relationships (spec §6.6, stories 58 to 60).

Driven through the HTTP API on the shared sample source: an editor adds a relationship by
hand (always ``accepted``, origin ``manual``, no confidence), may delete any relationship
that is not a declared foreign key, and later inference runs leave manual pairs alone.
"""

from __future__ import annotations

from dawam.modules.audit import AuditService
from tests.roles import RoleClients
from tests.sources import test_relationship_inference
from tests.sources.test_relationship_inference import (
    by_link,
    listed,
    run,
)
from tests.sources.test_schema_browser import schema

sample_system = test_relationship_inference.sample_system
"""The fixture that extracts the shared sample source into a Source System."""


def column_id(roles: RoleClients, system: str, ref: str) -> str:
    """The id of ``schema.table.column``."""
    db_schema, table, column = ref.split(".")
    for t in schema(roles, system)["tables"]:
        if (t["db_schema"], t["name"]) == (db_schema, table):
            return next(c["id"] for c in t["columns"] if c["name"] == column)
    raise AssertionError(ref)


def add(roles: RoleClients, system: str, source: str, target: str, as_role="editor"):
    return roles.client(as_role).post(
        f"{system}/relationships",
        json={
            "from_column_id": column_id(roles, system, source),
            "to_column_id": column_id(roles, system, target),
        },
    )


def code(response) -> str:
    return response.json()["error"]["code"]


def test_an_editor_adds_an_accepted_manual_relationship_without_confidence(
    roles: RoleClients,
    sample_system: str,
    app,
):
    created = add(roles, sample_system, "core.transactions.txn_id", "core.accounts.acct_no")

    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["origin"], body["status"], body["confidence"]) == ("manual", "accepted", None)
    assert body["decided_by"] and body["decided_at"]
    # Never hidden by the confidence filter, and listed with the others.
    found = by_link(listed(roles, sample_system, min_confidence=1))
    assert found["core.transactions.txn_id", "core.accounts.acct_no"]["id"] == body["id"]
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="relationship", entity_id=body["id"]
    )
    assert entry.old is None and entry.new["status"] == "accepted"
    assert entry.new["origin"] == "manual"


def test_blocked_pairs_and_roles(roles: RoleClients, sample_system: str):
    same = add(roles, sample_system, "core.accounts.acct_no", "core.accounts.acct_no")
    declared = add(roles, sample_system, "core.customers.branch_code", "core.branches.branch_code")
    reverse = add(roles, sample_system, "core.branches.branch_code", "core.customers.branch_code")
    assert (same.status_code, code(same)) == (422, "same_column")
    assert (declared.status_code, code(declared)) == (409, "declared_foreign_key")
    assert (reverse.status_code, code(reverse)) == (409, "declared_foreign_key")
    assert (
        add(
            roles, sample_system, "core.accounts.iban", "core.branches.branch_code", "viewer"
        ).status_code
        == 403
    )
    first = add(roles, sample_system, "core.accounts.iban", "core.branches.branch_code")
    again = add(roles, sample_system, "core.accounts.iban", "core.branches.branch_code")
    assert first.status_code == 201
    assert (again.status_code, code(again)) == (409, "already_accepted")
    missing = roles.client("editor").post(
        f"{sample_system}/relationships",
        json={
            "from_column_id": "00000000-0000-0000-0000-000000000000",
            "to_column_id": column_id(roles, sample_system, "core.accounts.acct_no"),
        },
    )
    assert missing.status_code == 404


def test_a_suggested_or_rejected_pair_becomes_the_manual_row(
    roles: RoleClients,
    sample_system: str,
):
    run(roles, sample_system)
    found = by_link(listed(roles, sample_system))
    suggested = found["core.accounts.cust_no", "core.customers.cust_no"]
    rejected = found["crm.contacts.cust_no", "core.customers.cust_no"]
    roles.client("editor").post(f"{sample_system}/relationships/{rejected['id']}/reject")

    a = add(roles, sample_system, "core.accounts.cust_no", "core.customers.cust_no")
    b = add(roles, sample_system, "crm.contacts.cust_no", "core.customers.cust_no")

    assert a.json()["id"] == suggested["id"] and b.json()["id"] == rejected["id"]
    for response in (a, b):
        assert response.status_code == 201
        assert (response.json()["origin"], response.json()["status"]) == ("manual", "accepted")
        assert response.json()["confidence"] is None


def test_inference_leaves_manual_pairs_alone(roles: RoleClients, sample_system: str):
    run(roles, sample_system)
    add(roles, sample_system, "core.accounts.cust_no", "core.customers.cust_no")
    run(roles, sample_system, threshold=0.95)

    item = by_link(listed(roles, sample_system, min_confidence=0))[
        "core.accounts.cust_no", "core.customers.cust_no"
    ]
    assert (item["origin"], item["status"], item["confidence"]) == ("manual", "accepted", None)


def test_deleting_rejects_inferred_and_removes_manual_rows(
    roles: RoleClients,
    sample_system: str,
    app,
):
    run(roles, sample_system)
    inferred = by_link(listed(roles, sample_system))[
        "crm.contacts.cust_no", "core.customers.cust_no"
    ]
    manual = add(roles, sample_system, "core.accounts.iban", "core.branches.branch_code").json()
    editor = roles.client("editor")

    assert editor.delete(f"{sample_system}/relationships/{inferred['id']}").status_code == 204
    assert editor.delete(f"{sample_system}/relationships/{manual['id']}").status_code == 204

    after = by_link(listed(roles, sample_system, min_confidence=0))
    assert after["crm.contacts.cust_no", "core.customers.cust_no"]["status"] == "rejected"
    assert ("core.accounts.iban", "core.branches.branch_code") not in after
    entries = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="relationship", entity_id=manual["id"]
    )
    [deleted] = [e for e in entries if e.new is None]
    assert deleted.new is None and deleted.old["status"] == "accepted"
    assert deleted.old["origin"] == "manual"
    assert editor.delete(f"{sample_system}/relationships/{manual['id']}").status_code == 404
    assert (
        roles.client("viewer").delete(f"{sample_system}/relationships/{inferred['id']}").status_code
        == 403
    )
