"""Relationship inference (spec §6.6, stories 58, 59).

Behaviour is driven through the HTTP API: an editor starts inference (a background job, run
inline in tests), any member lists the suggested relationships with their evidence, and an
editor accepts or rejects them. The shared sample source seeds undeclared relationships
(``accounts.cust_no``, ``transactions.acct_no``, ``crm.contacts.cust_no``); a scratch source
shows how value overlap (live Connection and profiling) changes the score. The audit trail
is read through its service.
"""

from __future__ import annotations

import pytest

from dawam.modules.audit import AuditService
from tests.roles import RoleClients
from tests.sample_source import SampleSource
from tests.sources import test_extraction
from tests.sources.test_extraction import add_system, connect, extract
from tests.sources.test_profiling import profile_tables
from tests.sources.test_schema_browser import schema
from tests.sources.test_schema_import import COLUMNS, csvs, upload

scratch_source = test_extraction.scratch_source

SOURCE = """
CREATE TABLE customers (id integer PRIMARY KEY, name text);
CREATE TABLE products (sku text PRIMARY KEY, title text);
CREATE TABLE orders (
    id integer PRIMARY KEY,
    customer_id integer,
    sku text,
    quantity integer
);
CREATE TABLE invoices (id integer PRIMARY KEY, customer_id integer);
CREATE TABLE shipments (id integer PRIMARY KEY, order_id integer REFERENCES orders (id));
INSERT INTO customers SELECT i, 'c' || i FROM generate_series(1, 10) i;
INSERT INTO products SELECT 'sku' || i, 't' || i FROM generate_series(1, 5) i;
INSERT INTO orders SELECT i, 1 + i % 10, 'sku' || (1 + i % 5), i FROM generate_series(1, 30) i;
-- invoices.customer_id looks right by name but none of its values exist in customers.
INSERT INTO invoices SELECT i, 500 + i FROM generate_series(1, 10) i;
INSERT INTO shipments SELECT i, i FROM generate_series(1, 10) i;
"""


def infer(roles: RoleClients, system: str, as_role="editor", **body):
    return roles.client(as_role).post(f"{system}/relationship-inference", json=body)


def run(roles: RoleClients, system: str, **body) -> dict:
    started = infer(roles, system, **body)
    assert started.status_code == 202, started.text
    job = roles.client("editor").get(f"/api/v1/jobs/{started.json()['job_id']}").json()
    assert job["type"] == "infer_relationships" and job["status"] == "succeeded", job
    return job


def listed(roles: RoleClients, system: str, as_role="viewer", **params) -> list[dict]:
    response = roles.client(as_role).get(f"{system}/relationships", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def link(item: dict) -> tuple[str, str]:
    """``from`` and ``to`` as ``schema.table.column``."""
    f, t = item["from_column"], item["to_column"]
    return (
        f"{f['db_schema']}.{f['table']}.{f['column']}",
        f"{t['db_schema']}.{t['table']}.{t['column']}",
    )


def by_link(items: list[dict]) -> dict[tuple[str, str], dict]:
    return {link(i): i for i in items}


@pytest.fixture
def sample_system(roles: RoleClients, sample_source: SampleSource) -> str:
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    return system


def test_the_seeded_undeclared_relationships_are_found_with_their_evidence(
    roles: RoleClients, sample_system: str
):
    run(roles, sample_system)

    found = by_link(listed(roles, sample_system))

    accounts_customer = found["core.accounts.cust_no", "core.customers.cust_no"]
    assert accounts_customer["origin"] == "routine" and accounts_customer["status"] == "suggested"
    assert accounts_customer["confidence"] >= 0.6
    signals = accounts_customer["evidence"]["signals"]
    assert signals["name"]["score"] == 0.9 and signals["type"]["score"] == 1.0
    assert signals["unique"]["detail"] == "primary key"
    assert "view core.customer_balances" in signals["join"]["objects"]
    assert "procedure core.close_account" in signals["join"]["objects"]
    assert "overlap" not in signals, "nothing is profiled yet"

    transactions_account = found["core.transactions.acct_no", "core.accounts.acct_no"]
    assert transactions_account["origin"] == "routine"
    assert (
        "function core.account_turnover"
        in transactions_account["evidence"]["signals"]["join"]["objects"]
    )

    contacts_customer = found["crm.contacts.cust_no", "core.customers.cust_no"]
    assert contacts_customer["origin"] == "inferred"
    assert "join" not in contacts_customer["evidence"]["signals"]
    assert 0.6 <= contacts_customer["confidence"] < accounts_customer["confidence"]

    # The declared foreign key is not proposed, and nothing falls below the threshold.
    assert ("core.customers.branch_code", "core.branches.branch_code") not in found
    assert all(i["confidence"] >= 0.6 for i in found.values())
    assert [i["confidence"] for i in found.values()] == sorted(
        (i["confidence"] for i in found.values()), reverse=True
    )


def test_a_threshold_hides_weaker_candidates(roles: RoleClients, sample_system: str):
    run(roles, sample_system)

    strict = by_link(listed(roles, sample_system, min_confidence=0.8))

    assert ("core.accounts.cust_no", "core.customers.cust_no") in strict
    assert ("crm.contacts.cust_no", "core.customers.cust_no") not in strict
    run(roles, sample_system, threshold=0.9)
    assert ("crm.contacts.cust_no", "core.customers.cust_no") not in by_link(
        listed(roles, sample_system, min_confidence=0)
    )


def test_name_type_and_join_rules_work_without_a_connection(roles: RoleClients):
    system = add_system(roles)
    sheets = csvs(
        tables="schema,table,kind,definition\n"
        "sales,Customer,table,\nsales,Orders,table,\n"
        "sales,Totals,view,SELECT 1 FROM sales.Orders o JOIN sales.Customer c ON o.cref = c.id\n",
        columns=COLUMNS + "sales,Orders,id,integer\nsales,Orders,customer_id,integer\n"
        "sales,Orders,cref,integer\nsales,Orders,note,text\nsales,Totals,x,integer\n",
        constraints="schema,table,constraint,type,columns\n"
        "sales,Customer,pk,pk,id\nsales,Orders,pk,pk,id\n",
    )
    outcome = upload(roles, system, sheets)
    assert outcome["imported"], outcome["report"]

    run(roles, system)

    found = by_link(listed(roles, system))
    by_name = found["sales.Orders.customer_id", "sales.Customer.id"]
    assert by_name["origin"] == "inferred" and "overlap" not in by_name["evidence"]["signals"]
    by_join = found["sales.Orders.cref", "sales.Customer.id"]
    assert by_join["origin"] == "routine"
    assert by_join["evidence"]["signals"]["join"]["objects"] == ["view sales.Totals"]
    assert not [k for k in found if k[0].endswith(".note")]


def test_value_overlap_needs_profiling_and_decides_between_look_alikes(
    roles: RoleClients, scratch_source
):
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)

    run(roles, system)

    before = by_link(listed(roles, system))
    assert ("public.orders.customer_id", "public.customers.id") in before
    assert ("public.invoices.customer_id", "public.customers.id") in before
    assert ("public.orders.sku", "public.products.sku") in before
    assert ("public.shipments.order_id", "public.orders.id") not in before, "declared"
    assert not [k for k in before if k[0].endswith(".quantity")]
    assert all("overlap" not in i["evidence"]["signals"] for i in before.values())

    tables = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}
    profiled = profile_tables(
        roles, system, [tables[n] for n in ("customers", "orders", "invoices", "products")]
    )
    assert profiled.status_code == 202, profiled.text
    run(roles, system)

    after = by_link(listed(roles, system))
    good = after["public.orders.customer_id", "public.customers.id"]
    assert good["evidence"]["signals"]["overlap"]["ratio"] == 1.0
    assert (
        good["confidence"]
        > before["public.orders.customer_id", "public.customers.id"]["confidence"]
    )
    assert (
        after["public.orders.sku", "public.products.sku"]["evidence"]["signals"]["overlap"]["ratio"]
        == 1.0
    )
    assert ("public.invoices.customer_id", "public.customers.id") not in after, (
        "no value of invoices.customer_id exists in customers.id"
    )
    assert ("public.invoices.customer_id", "public.customers.id") not in by_link(
        listed(roles, system, min_confidence=0)
    ), "a candidate that falls below the threshold is withdrawn"


def test_accepting_and_rejecting_are_audited_and_survive_a_new_run(
    roles: RoleClients, sample_system: str, app
):
    run(roles, sample_system)
    found = by_link(listed(roles, sample_system))
    keep = found["core.accounts.cust_no", "core.customers.cust_no"]
    drop = found["crm.contacts.cust_no", "core.customers.cust_no"]
    editor = roles.client("editor")

    accepted = editor.post(f"{sample_system}/relationships/{keep['id']}/accept")
    rejected = roles.client("owner").post(f"{sample_system}/relationships/{drop['id']}/reject")

    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert accepted.json()["decided_by"] and accepted.json()["version"] == 2
    assert rejected.json()["status"] == "rejected"
    audit = AuditService(app.state.engine)
    [entry] = audit.list(roles.workspace_id, entity_type="relationship", entity_id=keep["id"])
    assert (entry.old, entry.new, entry.via) == (
        {"status": "suggested"},
        {"status": "accepted"},
        "user",
    )
    assert (
        editor.post(f"{sample_system}/relationships/{keep['id']}/accept").json()["version"] == 2
    ), "deciding again changes nothing"

    run(roles, sample_system)

    after = by_link(listed(roles, sample_system))
    assert after["core.accounts.cust_no", "core.customers.cust_no"]["status"] == "accepted"
    assert after["crm.contacts.cust_no", "core.customers.cust_no"]["status"] == "rejected"
    assert [link(i) for i in listed(roles, sample_system, status="suggested")] and (
        "crm.contacts.cust_no",
        "core.customers.cust_no",
    ) not in [link(i) for i in listed(roles, sample_system, status="suggested")]


def test_roles_and_inputs(roles: RoleClients, sample_system: str):
    assert infer(roles, sample_system, as_role="viewer").status_code == 403
    assert infer(roles, sample_system, threshold=0).status_code == 422
    assert infer(roles, sample_system, sample_size=0).status_code == 422
    run(roles, sample_system)
    item = listed(roles, sample_system)[0]
    viewer = roles.client("viewer")
    assert viewer.post(f"{sample_system}/relationships/{item['id']}/accept").status_code == 403
    assert roles.client("editor").get(
        f"{sample_system}/relationships?status=maybe"
    ).status_code == (422)


def test_inference_needs_a_snapshot(roles: RoleClients):
    system = add_system(roles)

    response = infer(roles, system)

    assert response.status_code == 409 and response.json()["error"]["code"] == "no_snapshot"


def profiled_scratch(roles: RoleClients, scratch_source) -> tuple[str, dict]:
    scratch_source["run"](SOURCE)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    tables = {t["name"]: t for t in schema(roles, system)["tables"]}
    ids = [tables[n]["id"] for n in ("customers", "orders", "invoices", "products")]
    assert profile_tables(roles, system, ids).status_code == 202
    return system, tables


def test_a_target_larger_than_the_sample_leaves_overlap_unmeasured(
    roles: RoleClients, scratch_source
):
    system, _ = profiled_scratch(roles, scratch_source)

    run(roles, system, sample_size=5)  # customers has 10 rows

    found = by_link(listed(roles, system))
    good = found["public.orders.customer_id", "public.customers.id"]
    assert "overlap" not in good["evidence"]["signals"]
    assert good["confidence"] == 0.75, "scored with the no-overlap weights, not dropped"
    # products has exactly 5 rows, so the sample covers it and it is measured.
    assert "overlap" in found["public.orders.sku", "public.products.sku"]["evidence"]["signals"]


def test_protected_columns_take_no_part_in_value_overlap(roles: RoleClients, scratch_source):
    system, tables = profiled_scratch(roles, scratch_source)
    customers = tables["customers"]
    [key] = [c for c in customers["columns"] if c["name"] == "id"]
    flagged = roles.client("editor").patch(
        f"{system}/tables/{customers['id']}/columns/{key['id']}",
        json={"version": key["version"], "is_sensitive": True},
    )
    assert flagged.status_code == 200, flagged.text

    run(roles, system)

    found = by_link(listed(roles, system))
    protected = found["public.orders.customer_id", "public.customers.id"]
    assert "overlap" not in protected["evidence"]["signals"]
    assert protected["confidence"] == 0.75
    assert "overlap" in found["public.orders.sku", "public.products.sku"]["evidence"]["signals"]
