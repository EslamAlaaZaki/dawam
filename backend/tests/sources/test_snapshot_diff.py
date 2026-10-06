"""Diffing two Snapshots (spec story 52, §6.3).

The HTTP tests re-extract a scratch source after ``ALTER TABLE`` changes; the pure
comparison is also tested directly, since it needs no database.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace as NS

from dawam.modules.sources.internal.diff import diff_snapshots
from tests.roles import RoleClients
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract, latest, snapshots

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""


def diff(roles: RoleClients, system: str, snapshot_id: str, against: str) -> dict:
    response = roles.client("viewer").get(f"{system}/snapshots/{snapshot_id}/diff/{against}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_diff_lists_added_removed_and_changed_tables_and_columns(
    roles: RoleClients, scratch_source
):
    scratch_source["run"](
        "CREATE TABLE orders (id integer PRIMARY KEY, note text, legacy text);"
        "CREATE TABLE gone (id integer);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    first = latest(roles, system)
    scratch_source["run"](
        "ALTER TABLE orders ALTER COLUMN note TYPE varchar(20);"
        "ALTER TABLE orders DROP COLUMN legacy;"
        "ALTER TABLE orders ADD COLUMN placed_on date;"
        "DROP TABLE gone;"
        "CREATE TABLE fresh (id integer);"
    )
    extract(roles, system)
    second = latest(roles, system)

    result = diff(roles, system, second["id"], first["id"])

    assert (result["from_snapshot_id"], result["to_snapshot_id"]) == (first["id"], second["id"])
    tables = {t["name"]: t for t in result["tables"]}
    assert {name: t["change"] for name, t in tables.items()} == {
        "fresh": "added",
        "gone": "removed",
        "orders": "changed",
    }
    columns = {c["name"]: c for c in tables["orders"]["columns"]}
    assert {name: c["change"] for name, c in columns.items()} == {
        "legacy": "removed",
        "note": "changed",
        "placed_on": "added",
    }
    assert columns["note"]["fields"] == [
        {"field": "data_type", "before": "text", "after": "character varying(20)"}
    ]
    reverse = diff(roles, system, first["id"], second["id"])
    assert {t["name"]: t["change"] for t in reverse["tables"]} == {
        "fresh": "removed",
        "gone": "added",
        "orders": "changed",
    }
    assert diff(roles, system, first["id"], first["id"])["tables"] == []


def test_a_diff_needs_two_snapshots_of_the_source_system(roles: RoleClients, scratch_source):
    scratch_source["run"]("CREATE TABLE t (id integer);")
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    [only] = snapshots(roles, system)

    missing = roles.client("viewer").get(f"{system}/snapshots/{only['id']}/diff/{uuid.uuid4()}")

    assert missing.status_code == 404


def _column(name: str, data_type: str = "text", ordinal: int = 1):
    return NS(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        name=name,
        ordinal=ordinal,
        data_type=data_type,
        is_nullable=True,
        is_pk=False,
        default=None,
        comment=None,
    )


def _table(name: str, columns: list, table_id: uuid.UUID | None = None):
    return NS(
        id=table_id or uuid.uuid5(uuid.NAMESPACE_DNS, "table:" + name),
        db_schema="public",
        name=name,
        kind="table",
        view_definition=None,
        comment=None,
        columns=columns,
    )


def _content(tables: list, routines: list | None = None):
    return NS(snapshot=NS(id=uuid.uuid4()), db_schemas=[], tables=tables, routines=routines or [])


def test_an_identity_that_is_renamed_is_one_change_not_a_remove_and_an_add():
    shared = uuid.uuid4()
    old = _content([_table("Customer", [_column("a")], shared)])
    new = _content([_table("customer", [_column("a")], shared)])

    [change] = diff_snapshots(old, new).tables

    assert (change.change, change.name) == ("changed", "customer")
    assert [(f.field, f.before, f.after) for f in change.fields] == [
        ("name", "Customer", "customer")
    ]


def test_routines_are_compared_by_definition():
    routine = uuid.uuid4()

    def make(definition: str):
        return NS(
            id=routine,
            db_schema="public",
            name="f",
            kind="function",
            signature="()",
            definition=definition,
        )

    result = diff_snapshots(_content([], [make("select 1")]), _content([], [make("select 2")]))

    [change] = result.routines
    assert change.change == "changed"
    assert change.fields[0].field == "definition"
