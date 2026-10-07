"""The AI source query guard over a real extracted Snapshot (ADR 0002, story 68): the S1 pass.

An extraction fills the app database; ``load_guard_catalog`` reads the latest Snapshot with the
one Protected Column policy; ``check_query`` guards; the safe SQL it returns then runs against the
sample source as its read-only user. The adversarial table is in ``test_query_guard.py``.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi import FastAPI
from sqlalchemy.orm import Session

from dawam.modules.sources import (
    Rejected,
    SafeQuery,
    check_query,
    load_guard_catalog,
    untraceable_views,
)
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources.test_extraction import add_system, connect, extract, snapshots


def guard_catalog(app: FastAPI, roles: RoleClients, sample_source: SampleSource):
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    [latest] = [s for s in snapshots(roles, system) if s["is_latest"]]
    with Session(app.state.engine) as db:
        return load_guard_catalog(
            db,
            snapshot_id=uuid.UUID(latest["id"]),
            database=sample_source.database,
            allowed_schemas=("core", "crm"),
        )


def run_on_source(sample_source: SampleSource, sql: str) -> list[tuple]:
    user, password = sample_source.reader
    with psycopg.connect(
        host=sample_source.host,
        port=sample_source.port,
        dbname=sample_source.database,
        user=user,
        password=password,
        options="-c default_transaction_read_only=on",
    ) as conn:
        return conn.execute(sql).fetchall()  # type: ignore[arg-type]


def test_guarding_an_extracted_snapshot(
    app: FastAPI, roles: RoleClients, sample_source: SampleSource
):
    catalog = guard_catalog(app, roles, sample_source)

    def guard(sql: str) -> SafeQuery | Rejected:
        return check_query(sql, engine="postgresql", catalog=catalog)

    # protected by the name rules that ran on the new Snapshot: national_id, email, phone, iban...
    projected = guard("select national_id from core.customers")
    assert isinstance(projected, Rejected)
    assert "core.customers.national_id" in projected.reason

    # a derived value is returned, masked, and the safe SQL really runs on the source
    masked = guard("select cust_no, substring(national_id, 1, 3) as prefix from core.customers")
    assert isinstance(masked, SafeQuery)
    assert masked.masked_columns == ("prefix",)
    assert '"core"."customers"' in masked.sql
    assert len(run_on_source(sample_source, masked.sql)) > 0

    # counting and joining on protected columns is allowed
    joined = guard(
        "select count(distinct c.national_id) from core.customers c "
        "join core.accounts a on a.cust_no = c.cust_no"
    )
    assert isinstance(joined, SafeQuery)
    assert joined.masked_columns == ()
    [(count,)] = run_on_source(sample_source, joined.sql)
    assert count >= 0

    # SELECT * is expanded from the Snapshot; protected columns come back masked
    star = guard("select * from core.customers")
    assert isinstance(star, SafeQuery)
    assert "*" not in star.sql
    assert {"national_id", "email"} <= set(star.masked_columns)
    assert "cust_no" not in star.masked_columns

    # views are traced: customer_balances reads customers.full_name (a protected name)
    view = guard("select * from core.customer_balances")
    assert isinstance(view, SafeQuery)
    assert "full_name" in view.masked_columns
    assert "total_balance" not in view.masked_columns
    assert len(run_on_source(sample_source, view.sql)) > 0
    assert untraceable_views(engine="postgresql", catalog=catalog) == ()

    # outside the allowed schemas, and every way of escaping them
    for sql in [
        "select * from restricted.salaries",
        "select * from pg_catalog.pg_user",
        f"select * from {sample_source.database}.core.nowhere",
        "select pg_sleep(1)",
        "select cust_no from core.customers; drop table core.customers",
    ]:
        assert isinstance(guard(sql), Rejected), sql
