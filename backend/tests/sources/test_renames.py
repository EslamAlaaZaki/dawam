"""Rename candidates and manual merges (spec story 52a, §7 "Source identity vs Snapshots").

The HTTP tests rename objects in a scratch source and re-extract; the pairing itself is
also tested directly, since it needs no database.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI

from dawam.modules.audit import AuditService
from dawam.modules.sources.internal.renames import (
    ColumnShape,
    SchemaShape,
    TableShape,
    column_matches,
    schema_matches,
    table_matches,
)
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract, latest, snapshots
from tests.sources.test_schema_browser import schema
from tests.sources.test_snapshot_diff import diff

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""


def candidates(roles: RoleClients, system: str) -> list[dict]:
    response = roles.client("viewer").get(f"{system}/rename-candidates")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def confirm(roles: RoleClients, system: str, candidate: dict, as_role="editor"):
    return roles.client(as_role).post(f"{system}/rename-candidates/{candidate['id']}/confirm")


def table_of(roles: RoleClients, system: str, name: str) -> dict:
    [table] = [t for t in schema(roles, system)["tables"] if t["name"] == name]
    return table


def orders_source(scratch_source, system_roles: RoleClients) -> str:
    scratch_source["run"](
        "CREATE TABLE orders (id integer PRIMARY KEY, cust_no text, amount numeric);"
    )
    system = add_system(system_roles)
    connect(system_roles, system, scratch_source["body"]())
    extract(system_roles, system)
    return system


def test_a_renamed_column_is_proposed_and_confirming_keeps_its_identity(
    roles: RoleClients, scratch_source
):
    system = orders_source(scratch_source, roles)
    first = latest(roles, system)
    column = {c["name"]: c for c in table_of(roles, system, "orders")["columns"]}["cust_no"]
    described = roles.client("editor").patch(
        f"{system}/tables/{table_of(roles, system, 'orders')['id']}/columns/{column['id']}",
        json={"version": column["version"], "description": "Customer number"},
    )
    assert described.status_code == 200, described.text
    scratch_source["run"]("ALTER TABLE orders RENAME COLUMN cust_no TO customer_no;")
    extract(roles, system)

    [candidate] = candidates(roles, system)

    assert (candidate["object_type"], candidate["old_name"], candidate["new_name"]) == (
        "column",
        "cust_no",
        "customer_no",
    )
    assert candidate["location"] == "public.orders"
    assert 0.5 <= candidate["confidence"] <= 1
    assert candidate["old_object_id"] == column["id"]

    response = confirm(roles, system, candidate)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == column["id"] and response.json()["previous_name"] == "cust_no"
    columns = {c["name"]: c for c in table_of(roles, system, "orders")["columns"]}
    assert "cust_no" not in columns
    assert columns["customer_no"]["id"] == column["id"]
    assert columns["customer_no"]["description"] == "Customer number"
    assert columns["customer_no"]["status"] == "present"
    assert candidates(roles, system) == []
    # The rename is one change between the two Snapshots, not a drop and an add.
    second = latest(roles, system)
    [change] = diff(roles, system, second["id"], first["id"])["tables"][0]["columns"]
    assert (change["change"], change["id"]) == ("changed", column["id"])
    assert change["fields"][0] == {"field": "name", "before": "cust_no", "after": "customer_no"}
    # And the next extraction matches the identity by its new name.
    extract(roles, system)
    assert candidates(roles, system) == []


def test_rejecting_a_candidate_leaves_the_removal_and_the_addition(
    roles: RoleClients, scratch_source
):
    system = orders_source(scratch_source, roles)
    scratch_source["run"]("ALTER TABLE orders RENAME COLUMN cust_no TO customer_no;")
    extract(roles, system)
    [candidate] = candidates(roles, system)

    response = roles.client("editor").post(f"{system}/rename-candidates/{candidate['id']}/reject")

    assert response.status_code == 204
    assert candidates(roles, system) == []
    content = schema(roles, system)
    assert [c["name"] for c in content["removed_columns"]] == ["cust_no"]
    assert "customer_no" in [c["name"] for c in table_of(roles, system, "orders")["columns"]]
    again = confirm(roles, system, candidate)
    assert again.status_code == 409 and again.json()["error"]["code"] == "rename_conflict"


def test_a_renamed_table_is_proposed_and_keeps_its_columns(roles: RoleClients, scratch_source):
    system = orders_source(scratch_source, roles)
    table = table_of(roles, system, "orders")
    scratch_source["run"]("ALTER TABLE orders RENAME TO sales_orders;")
    extract(roles, system)

    [candidate] = candidates(roles, system)

    assert (candidate["object_type"], candidate["old_name"], candidate["new_name"]) == (
        "table",
        "orders",
        "sales_orders",
    )
    assert confirm(roles, system, candidate).status_code == 200
    renamed = table_of(roles, system, "sales_orders")
    assert renamed["id"] == table["id"]
    assert [c["id"] for c in renamed["columns"]] == [c["id"] for c in table["columns"]]
    assert all(c["status"] == "present" for c in renamed["columns"])
    assert [t["name"] for t in schema(roles, system)["tables"]] == ["sales_orders"]


def test_a_renamed_database_schema_is_proposed_and_keeps_its_tables(
    roles: RoleClients, scratch_source
):
    scratch_source["run"]("CREATE SCHEMA legacy; CREATE TABLE legacy.orders (id integer);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"](("legacy", "modern")))
    extract(roles, system)
    [before] = latest(roles, system)["db_schemas"]
    table = table_of(roles, system, "orders")
    scratch_source["run"]("ALTER SCHEMA legacy RENAME TO modern;")
    extract(roles, system)

    [candidate] = [c for c in candidates(roles, system) if c["object_type"] == "db_schema"]

    assert (candidate["old_name"], candidate["new_name"]) == ("legacy", "modern")
    assert confirm(roles, system, candidate).status_code == 200
    [after] = latest(roles, system)["db_schemas"]
    assert (after["id"], after["name"]) == (before["id"], "modern")
    assert table_of(roles, system, "orders")["id"] == table["id"]
    assert candidates(roles, system) == []


def test_unlike_objects_are_not_proposed(roles: RoleClients, scratch_source):
    system = orders_source(scratch_source, roles)
    scratch_source["run"](
        "ALTER TABLE orders DROP COLUMN cust_no; ALTER TABLE orders ADD COLUMN placed_on date;"
    )
    extract(roles, system)

    assert candidates(roles, system) == []


def test_a_rename_noticed_late_is_merged_by_hand_and_audited(
    roles: RoleClients, scratch_source, app: FastAPI
):
    system = orders_source(scratch_source, roles)
    column = {c["name"]: c for c in table_of(roles, system, "orders")["columns"]}["cust_no"]
    scratch_source["run"]("ALTER TABLE orders RENAME COLUMN cust_no TO customer_no;")
    extract(roles, system)
    [candidate] = candidates(roles, system)
    roles.client("editor").post(f"{system}/rename-candidates/{candidate['id']}/reject")
    added = candidate["new_object_id"]

    response = roles.client("editor").post(
        f"{system}/renames",
        json={"object_type": "column", "removed_id": column["id"], "added_id": added},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "customer_no"
    columns = {c["name"]: c for c in table_of(roles, system, "orders")["columns"]}
    assert columns["customer_no"]["id"] == column["id"]
    assert schema(roles, system)["removed_columns"] == []
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="source_column", entity_id=column["id"]
    )
    assert entry.actor_id == roles.user("editor").id and entry.via == "user"
    assert entry.old == {"name": "cust_no"}
    assert entry.new == {"name": "customer_no", "merged_object_id": added}


def test_a_confirmed_rename_is_audited(roles: RoleClients, scratch_source, app: FastAPI):
    system = orders_source(scratch_source, roles)
    scratch_source["run"]("ALTER TABLE orders RENAME TO sales_orders;")
    extract(roles, system)
    [candidate] = candidates(roles, system)

    confirm(roles, system, candidate)

    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="source_table", entity_id=candidate["old_object_id"]
    )
    assert (entry.old, entry.new["name"]) == ({"name": "orders"}, "sales_orders")


def test_only_a_removed_object_merges_into_an_added_one_of_its_kind(
    roles: RoleClients, scratch_source
):
    system = orders_source(scratch_source, roles)
    columns = {c["name"]: c for c in table_of(roles, system, "orders")["columns"]}
    merge = lambda body: roles.client("editor").post(f"{system}/renames", json=body)  # noqa: E731

    two_present = merge(
        {
            "object_type": "column",
            "removed_id": columns["cust_no"]["id"],
            "added_id": columns["amount"]["id"],
        }
    )
    wrong_kind = merge(
        {
            "object_type": "table",
            "removed_id": columns["cust_no"]["id"],
            "added_id": columns["amount"]["id"],
        }
    )
    unknown = merge(
        {
            "object_type": "column",
            "removed_id": str(uuid.uuid4()),
            "added_id": columns["amount"]["id"],
        }
    )

    for response in (two_present, wrong_kind, unknown):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "rename_conflict"
    assert len(snapshots(roles, system)) == 1


def test_viewers_list_candidates_but_cannot_confirm_them(roles: RoleClients, scratch_source):
    system = orders_source(scratch_source, roles)
    scratch_source["run"]("ALTER TABLE orders RENAME COLUMN cust_no TO customer_no;")
    extract(roles, system)
    [candidate] = candidates(roles, system)

    assert confirm(roles, system, candidate, as_role="viewer").status_code == 403


# -- the pairing ---------------------------------------------------------------------------

TABLE = uuid.uuid4()


def _column(name: str, data_type="text", ordinal=2, table=TABLE) -> ColumnShape:
    return ColumnShape(uuid.uuid4(), table, name, data_type, ordinal)


def test_columns_pair_on_the_same_table_type_and_position():
    old = _column("CUST_NO")
    new = _column("CUSTOMER_NO")

    [match] = column_matches([old], [new])

    assert (match.old_id, match.new_id, match.new_name) == (old.id, new.id, "CUSTOMER_NO")
    assert 0.5 < match.confidence < 1
    assert column_matches([old], [_column("CUSTOMER_NO", data_type="integer")]) == []
    assert column_matches([old], [_column("CUSTOMER_NO", ordinal=3)]) == []
    assert column_matches([old], [_column("CUSTOMER_NO", table=uuid.uuid4())]) == []


def test_a_closer_name_wins_and_each_column_pairs_once():
    old = _column("cust_no", ordinal=2)
    other = _column("remark", ordinal=2)
    close = _column("cust_nr", ordinal=2)
    far = _column("zzz", ordinal=2)

    matches = column_matches([old, other], [close, far])

    assert {(m.old_id, m.new_id) for m in matches} == {(old.id, close.id), (other.id, far.id)}


def _table(name: str, columns: set[str], schema_id=TABLE, kind="table") -> TableShape:
    return TableShape(uuid.uuid4(), schema_id, name, kind, frozenset(columns))


def test_tables_pair_on_similar_column_sets():
    old = _table("orders", {"id", "customer", "amount", "placed_on"})
    new = _table("sales_orders", {"id", "customer", "amount", "placed_on", "channel"})

    [match] = table_matches([old], [new])

    assert (match.old_id, match.new_id) == (old.id, new.id)
    assert match.confidence > 0.6
    assert table_matches([old], [_table("x", {"a", "b"})]) == []
    assert table_matches([old], [_table("x", set(old.columns), schema_id=uuid.uuid4())]) == []
    assert table_matches([old], [_table("x", set(old.columns), kind="view")]) == []


def test_schemas_pair_on_similar_table_sets():
    old = SchemaShape(uuid.uuid4(), "legacy", frozenset({"a", "b", "c"}))
    new = SchemaShape(uuid.uuid4(), "modern", frozenset({"a", "b", "c", "d"}))

    [match] = schema_matches([old], [new])

    assert (match.old_id, match.new_id) == (old.id, new.id)
    assert schema_matches([old], [SchemaShape(uuid.uuid4(), "x", frozenset({"z"}))]) == []
    assert schema_matches([SchemaShape(uuid.uuid4(), "empty", frozenset())], [new]) == []
