"""The Source Schema browser and name search (spec story 54, §6.3).

Behaviour is driven through the HTTP API: members read the latest Snapshot plus the
Source Objects it no longer has, and search it by name. The 2 000-table search seeds a
synthetic catalog through the Snapshot writer, since extracting one takes minutes.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy.orm import Session

from dawam.modules.sources.internal.connector import (
    ColumnInfo,
    RoutineInfo,
    SourceCatalog,
    TableInfo,
)
from dawam.modules.sources.internal.snapshots import store_catalog
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract

scratch_source = test_extraction.scratch_source
"""The fixture that gives a test a source database of its own."""


def schema(roles: RoleClients, system: str, as_role="viewer") -> dict:
    response = roles.client(as_role).get(f"{system}/schema")
    assert response.status_code == 200, response.text
    return response.json()


def search(roles: RoleClients, system: str, q: str, as_role="viewer", **params) -> list[dict]:
    response = roles.client(as_role).get(f"{system}/schema/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_the_source_schema_is_the_latest_snapshot_with_every_object_present(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)

    content = schema(roles, system)

    assert content["is_latest"] and content["table_count"] == 7
    assert [s["name"] for s in content["db_schemas"]] == ["core", "crm"]
    objects = [*content["db_schemas"], *content["tables"], *content["routines"]]
    objects += [c for t in content["tables"] for c in t["columns"]]
    assert {o["status"] for o in objects} == {"present"}
    assert content["removed_tables"] == []


def test_a_source_schema_needs_a_snapshot(roles: RoleClients):
    system = add_system(roles)

    response = roles.client("viewer").get(f"{system}/schema")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_removed_and_out_of_scope_objects_are_shown_with_their_state(
    roles: RoleClients, scratch_source
):
    scratch_source["run"](
        "CREATE SCHEMA sales; CREATE TABLE sales.invoices (id integer);"
        "CREATE TABLE public.orders (id integer PRIMARY KEY, legacy text);"
        "CREATE TABLE public.gone (id integer);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"](("public", "sales")))
    extract(roles, system)
    scratch_source["run"]("DROP TABLE gone; ALTER TABLE orders DROP COLUMN legacy;")
    connect(roles, system, scratch_source["body"](("public",)))

    extract(roles, system)

    content = schema(roles, system)
    removed = {(t["db_schema"], t["name"]): t for t in content["removed_tables"]}
    assert set(removed) == {("public", "gone"), ("sales", "invoices")}
    assert removed["public", "gone"]["status"] == "source_removed"
    assert removed["sales", "invoices"]["status"] == "out_of_scope"
    assert [t["name"] for t in content["tables"]] == ["orders"]
    [orders] = content["tables"]
    assert {c["name"]: c["status"] for c in orders["columns"]} == {"id": "present"}
    [legacy] = content["removed_columns"]
    assert (legacy["table_id"], legacy["name"], legacy["status"], legacy["data_type"]) == (
        orders["id"],
        "legacy",
        "source_removed",
        "text",
    )


def test_search_finds_tables_columns_routines_and_schemas_by_name(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)

    found = {
        (r["kind"], r["db_schema"], r["table"], r["name"]) for r in search(roles, system, "CUST")
    }

    assert ("table", "core", None, "customers") in found
    assert ("view", "core", None, "customer_balances") in found
    assert ("column", "core", "accounts", "cust_no") in found
    assert {kind for kind, *_ in found} == {"table", "view", "column"}
    assert {(r["kind"], r["name"]) for r in search(roles, system, "turnover")} == {
        ("routine", "account_turnover")
    }
    assert {(r["kind"], r["name"]) for r in search(roles, system, "crm")} == {("db_schema", "crm")}
    assert search(roles, system, "no-such-thing") == []


def test_search_ranks_exact_then_prefix_matches_and_treats_wildcards_literally(
    roles: RoleClients, scratch_source
):
    scratch_source["run"](
        'CREATE TABLE "a_b" (id integer); CREATE TABLE axb (id integer);'
        "CREATE TABLE my_orders (id integer); CREATE TABLE orders (id integer);"
        "CREATE TABLE orders_archive (id integer);"
    )
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)

    names = [r["name"] for r in search(roles, system, "orders") if r["kind"] == "table"]
    assert names == ["orders", "orders_archive", "my_orders"]
    assert [r["name"] for r in search(roles, system, "a_b")] == ["a_b"]
    limited = search(roles, system, "orders", limit=2)
    assert [r["name"] for r in limited] == ["orders", "orders_archive"]


def test_search_needs_a_snapshot_and_an_empty_query_finds_nothing(
    roles: RoleClients, sample_source: SampleSource
):
    system = add_system(roles)
    missing = roles.client("viewer").get(f"{system}/schema/search", params={"q": "x"})
    assert missing.status_code == 404
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    assert search(roles, system, "") == search(roles, system, "   ") == []


def test_search_answers_in_under_300_ms_on_a_2000_table_source(roles: RoleClients, app):
    system = add_system(roles)
    system_id = uuid.UUID(system.rsplit("/", 1)[1])
    column_names = ("id", "code", "name", "amount", "note", "flag", "x", "y")
    catalog = SourceCatalog(
        schemas=("public",),
        tables=tuple(
            TableInfo(
                schema="public",
                name=f"t{i}",
                kind="table",
                row_estimate=i,
                comment=None,
                definition=None,
                columns=tuple(
                    ColumnInfo(f"{name}_{i}", n, "text", True, False, None, None)
                    for n, name in enumerate(column_names, 1)
                ),
            )
            for i in range(2000)
        ),
        routines=(RoutineInfo("public", "r", "function", "SELECT 1", "x integer"),),
    )
    with Session(app.state.engine) as db, db.begin():
        store_catalog(
            db,
            source_system_id=system_id,
            catalog=catalog,
            allowed_schemas=("public",),
            origin="connection",
            job_id=None,
            taken_at=app.state.services.clock(),
            folds_case=False,
        )
    search(roles, system, "warm-up")

    started = time.monotonic()
    items = search(roles, system, "name_19", limit=200)
    elapsed = time.monotonic() - started

    assert len(items) == 111  # name_19, name_190..name_199 and name_1900..name_1999
    assert elapsed < 0.3, f"search took {elapsed * 1000:.0f} ms"
