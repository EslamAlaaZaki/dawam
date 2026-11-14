"""Relationship inference (spec §6.6, stories 58, 59, #608).

Behaviour is driven through the HTTP API: an editor starts inference (a background job, run
inline in tests), any member lists the suggested relationships with their evidence, and an
editor accepts or rejects them. The shared sample source seeds undeclared relationships
(``accounts.cust_no``, ``transactions.acct_no``, ``crm.contacts.cust_no``); a scratch source
shows how the count queries on the source set the confidence. The AI's guesses come from the
scripted fake provider behind a registered agent model. The audit trail is read through its
service.
"""

from __future__ import annotations

import json

import psycopg
import pytest
import sqlalchemy as sa

from dawam.modules.audit import AuditService
from dawam.modules.llm import Reply
from dawam.modules.sources import relationship_service
from tests.llm.test_roles_budgets import register, use_models
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

EMPLOYEES = """
CREATE TABLE employees (emp_id integer PRIMARY KEY, mgr integer);
INSERT INTO employees SELECT i, CASE WHEN i > 6 THEN 1 + i % 6 END FROM generate_series(1, 20) i;
"""
"""``employees.mgr`` references ``employees.emp_id``: a self-reference no name rule finds."""


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


def guesses(*pairs: tuple[str, str, float, str]) -> Reply:
    return Reply(
        text=json.dumps(
            {
                "relationships": [
                    {"child": c, "parent": p, "confidence": conf, "reason": why}
                    for c, p, conf, why in pairs
                ]
            }
        )
    )


def chats(fake_llm):
    return [c for c in fake_llm.calls if c.kind == "chat"]


def shown(call) -> dict:
    """The metadata the AI was shown (the JSON inside the user turn's <data> tags)."""
    text = next(m.content for m in call.messages if m.role == "user")
    return json.loads(text.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])


@pytest.fixture
def model(admin_client, fake_llm, roles: RoleClients):
    assert roles.workspace_id
    agent = register(admin_client, "relationship-model")
    assert use_models(roles.client("owner"), roles.workspace_id, agent).status_code == 200
    fake_llm.calls.clear()
    return agent


@pytest.fixture
def sample_system(roles: RoleClients, sample_source: SampleSource) -> str:
    system = add_system(roles)
    connect(roles, system, sample_source.connection_body())
    extract(roles, system)
    return system


def scratch_system(roles: RoleClients, scratch_source, *statements: str) -> str:
    for sql in statements:
        scratch_source["run"](sql)
    system = add_system(roles)
    connect(roles, system, scratch_source["body"]())
    extract(roles, system)
    return system


def test_the_seeded_undeclared_relationships_are_found_with_their_evidence(
    roles: RoleClients, sample_system: str
):
    job = run(roles, sample_system, threshold=0.5)

    found = by_link(listed(roles, sample_system, min_confidence=0.5))

    accounts_customer = found["core.accounts.cust_no", "core.customers.cust_no"]
    assert accounts_customer["origin"] == "routine" and accounts_customer["status"] == "suggested"
    evidence = accounts_customer["evidence"]
    assert evidence["found_by"] == "rules"
    signals = evidence["signals"]
    assert signals["name"]["score"] == 0.9 and signals["type"]["score"] == 1.0
    assert signals["unique"]["detail"] == "primary key"
    assert "view core.customer_balances" in signals["join"]["objects"]
    assert "procedure core.close_account" in signals["join"]["objects"]
    counts = evidence["counts"]
    assert counts["status"] == "counted" and counts["target_unique"] is True
    assert (counts["child_values"], counts["child_distinct"], counts["matched_rows"]) == (3, 2, 3)
    assert counts["orphan_rows"] == 0 and counts["match_rate"] == 1.0
    # 3 rows, 2 distinct customers: a possible chance match, so the match rate is halved.
    assert accounts_customer["confidence"] == pytest.approx(0.7 * 0.5 + 0.3 * 1.0)

    transactions_account = found["core.transactions.acct_no", "core.accounts.acct_no"]
    assert transactions_account["origin"] == "routine"
    assert (
        "function core.account_turnover"
        in transactions_account["evidence"]["signals"]["join"]["objects"]
    )

    contacts_customer = found["crm.contacts.cust_no", "core.customers.cust_no"]
    assert contacts_customer["origin"] == "inferred"
    assert "join" not in contacts_customer["evidence"]["signals"]
    assert contacts_customer["confidence"] == pytest.approx(0.7 * 0.5 + 0.3 * 0.71)

    # The declared foreign key is not proposed, and nothing falls below the threshold.
    assert ("core.customers.branch_code", "core.branches.branch_code") not in found
    assert all(i["confidence"] >= 0.5 for i in found.values())
    assert [i["confidence"] for i in found.values()] == sorted(
        (i["confidence"] for i in found.values()), reverse=True
    )
    assert "The AI is not set up" in job["log"] or "no agent model" in job["log"]


def test_a_threshold_hides_weaker_candidates(roles: RoleClients, sample_system: str):
    run(roles, sample_system, threshold=0.5)

    strict = by_link(listed(roles, sample_system))

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

    job = run(roles, system)

    found = by_link(listed(roles, system))
    by_name = found["sales.Orders.customer_id", "sales.Customer.id"]
    assert by_name["origin"] == "inferred" and by_name["confidence"] == 0.75
    assert by_name["evidence"]["counts"]["status"] == "no_connection"
    by_join = found["sales.Orders.cref", "sales.Customer.id"]
    assert by_join["origin"] == "routine" and by_join["confidence"] == pytest.approx(0.8)
    assert by_join["evidence"]["signals"]["join"]["objects"] == ["view sales.Totals"]
    assert not [k for k in found if k[0].endswith(".note")]
    assert "No live Connection" in job["log"]


def test_counts_on_the_source_decide_between_look_alikes(roles: RoleClients, scratch_source):
    system = scratch_system(roles, scratch_source, SOURCE)

    run(roles, system)

    found = by_link(listed(roles, system))
    good = found["public.orders.customer_id", "public.customers.id"]
    counts = good["evidence"]["counts"]
    assert counts["status"] == "counted" and counts["match_rate"] == 1.0
    assert (counts["child_values"], counts["child_distinct"], counts["orphan_rows"]) == (30, 10, 0)
    assert (counts["target_rows"], counts["target_distinct"]) == (10, 10)
    assert good["confidence"] == pytest.approx(0.7 + 0.3 * 0.75)
    sku = found["public.orders.sku", "public.products.sku"]
    assert sku["evidence"]["counts"]["match_rate"] == 1.0
    assert sku["confidence"] == pytest.approx(0.7 + 0.3 * 0.71)
    assert ("public.shipments.order_id", "public.orders.id") not in found, "declared"
    assert not [k for k in found if k[0].endswith(".quantity")]
    assert ("public.invoices.customer_id", "public.customers.id") not in by_link(
        listed(roles, system, min_confidence=0)
    ), "no value of invoices.customer_id exists in customers.id: below the threshold"


def test_accepting_and_rejecting_are_audited_and_survive_a_new_run(
    roles: RoleClients, sample_system: str, app
):
    run(roles, sample_system, threshold=0.5)
    found = by_link(listed(roles, sample_system, min_confidence=0.5))
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

    run(roles, sample_system, threshold=0.5)

    after = by_link(listed(roles, sample_system, min_confidence=0.5))
    assert after["core.accounts.cust_no", "core.customers.cust_no"]["status"] == "accepted"
    assert after["crm.contacts.cust_no", "core.customers.cust_no"]["status"] == "rejected"
    suggested = [link(i) for i in listed(roles, sample_system, status="suggested")]
    assert suggested and ("crm.contacts.cust_no", "core.customers.cust_no") not in suggested


def test_roles_and_inputs(roles: RoleClients, sample_system: str):
    assert infer(roles, sample_system, as_role="viewer").status_code == 403
    assert infer(roles, sample_system, threshold=0).status_code == 422
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


def test_a_count_over_the_timeout_is_cancelled_on_the_source_and_lowers_the_guess(
    roles: RoleClients, scratch_source, fresh_database_url, monkeypatch
):
    system = scratch_system(roles, scratch_source, SOURCE)
    monkeypatch.setattr(relationship_service, "COUNT_TIMEOUT_SECONDS", 1)
    url = sa.make_url(fresh_database_url)
    lock = psycopg.connect(
        host=url.host,
        port=url.port,
        user=url.username,
        password=url.password,
        dbname=url.database,
    )
    try:
        # Another session holds customers, so every count that reads it waits past 1 s.
        lock.execute("LOCK TABLE customers IN ACCESS EXCLUSIVE MODE")

        job = run(roles, system)

        waiting = lock.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name = 'dawam' AND state = 'active' AND pid <> pg_backend_pid()"
        ).fetchone()
    finally:
        lock.rollback()
        lock.close()

    assert waiting == (0,), "the source cancelled the query: nothing keeps running there"
    assert "count timed out" in job["log"]
    found = by_link(listed(roles, system))
    slow = found["public.orders.customer_id", "public.customers.id"]
    assert slow["evidence"]["counts"] == {"status": "timed_out", "note": "count timed out"}
    assert slow["confidence"] == pytest.approx(max(0.6, 0.75 - 0.15))
    assert slow["status"] == "suggested", "still visible, and the user can still accept it"
    # products is not locked: its pair is counted as usual.
    sku = found["public.orders.sku", "public.products.sku"]
    assert sku["evidence"]["counts"]["status"] == "counted"


def test_protected_columns_are_only_joined_and_counted(roles: RoleClients, scratch_source):
    system = scratch_system(roles, scratch_source, SOURCE)
    tables = {t["name"]: t for t in schema(roles, system)["tables"]}
    customers = tables["customers"]
    [key] = [c for c in customers["columns"] if c["name"] == "id"]
    flagged = roles.client("editor").patch(
        f"{system}/tables/{customers['id']}/columns/{key['id']}",
        json={"version": key["version"], "is_sensitive": True},
    )
    assert flagged.status_code == 200, flagged.text

    job = run(roles, system)

    found = by_link(listed(roles, system))
    protected = found["public.orders.customer_id", "public.customers.id"]
    counts = protected["evidence"]["counts"]
    assert counts["status"] == "counted" and counts["match_rate"] == 1.0
    assert protected["confidence"] == pytest.approx(0.7 + 0.3 * 0.75)
    # Numbers only: no value of the protected column is kept anywhere.
    kept = json.dumps(protected["evidence"]) + job["log"]
    assert all(isinstance(v, (int, float, bool, str)) for v in counts.values())
    assert "c1" not in kept and "sku1" not in kept


# -- the AI beside the rules ---------------------------------------------------------------


def test_ai_and_rule_pairs_are_unioned_and_a_self_reference_is_found(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system = scratch_system(roles, scratch_source, SOURCE, EMPLOYEES)
    fake_llm.script(
        guesses(
            ("public.employees.mgr", "public.employees.emp_id", 0.8, "mgr is a manager's emp_id"),
            # The rules' pair, the other way round: one pair, found by both.
            ("public.customers.id", "public.orders.customer_id", 0.9, "orders name a customer"),
            ("public.orders.quantity", "public.nowhere.id", 0.9, "not a column: dropped"),
            ("public.orders.quantity", "public.customers.id", 0.2, "under the threshold"),
        )
    )

    job = run(roles, system)

    [call] = chats(fake_llm)
    data = shown(call)
    assert {t["table"] for t in data["tables"]} >= {"public.employees", "public.orders"}
    prompt = "\n".join(m.content or "" for m in call.messages)
    assert "sku1" not in prompt and "'c1'" not in prompt, "metadata only, never a value"
    assert all("profile" not in c for t in data["tables"] for c in t["columns"])
    found = by_link(listed(roles, system))
    both = found["public.orders.customer_id", "public.customers.id"]
    assert both["evidence"]["found_by"] == "both" and both["origin"] == "inferred"
    assert both["evidence"]["ai_guess"] == {
        "confidence": 0.9,
        "reason": "orders name a customer",
    }
    assert both["confidence"] == pytest.approx(0.7 + 0.3 * 0.95)
    assert ("public.customers.id", "public.orders.customer_id") not in found
    self_reference = found["public.employees.mgr", "public.employees.emp_id"]
    assert self_reference["origin"] == "ai" and self_reference["evidence"]["found_by"] == "ai"
    assert self_reference["evidence"]["counts"]["child_distinct"] == 6
    assert self_reference["confidence"] == pytest.approx(0.7 + 0.3 * 0.8)
    assert not [k for k in found if k[0].endswith(".quantity")]
    assert "The AI found 2 pairs" in job["log"]


def test_an_ai_failure_still_saves_the_rules_pairs(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system = scratch_system(roles, scratch_source, SOURCE)
    fake_llm.script(Reply(text="this is not JSON"))

    job = run(roles, system)

    assert "The AI did not guess" in job["log"] and "not valid JSON" in job["log"]
    found = by_link(listed(roles, system))
    rules = found["public.orders.customer_id", "public.customers.id"]
    assert rules["evidence"]["found_by"] == "rules"
    assert rules["evidence"]["counts"]["status"] == "counted"


def test_the_ai_sees_profile_statistics_only_when_the_workspace_shares_them(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system = scratch_system(roles, scratch_source, SOURCE)
    tables = {t["name"]: t["id"] for t in schema(roles, system)["tables"]}
    assert profile_tables(roles, system, [tables["customers"]]).status_code == 202
    assert (
        use_models(
            roles.client("owner"), roles.workspace_id, model, share_profiles=True
        ).status_code
        == 200
    )
    fake_llm.calls.clear()
    fake_llm.script(guesses())

    run(roles, system)

    [call] = chats(fake_llm)
    [customers] = [t for t in shown(call)["tables"] if t["table"] == "public.customers"]
    profiled = {c["name"]: c.get("profile") for c in customers["columns"]}
    assert profiled["id"] == {"rows": 10, "null_pct": 0.0, "distinct": 10}
    assert "top_values" not in json.dumps(shown(call))


def test_an_ai_only_pair_is_withdrawn_when_a_new_run_no_longer_finds_it(
    roles: RoleClients, scratch_source, model, fake_llm
):
    system = scratch_system(roles, scratch_source, SOURCE, EMPLOYEES)
    fake_llm.script(guesses(("public.employees.mgr", "public.employees.emp_id", 0.8, "manager")))
    run(roles, system)
    assert ("public.employees.mgr", "public.employees.emp_id") in by_link(listed(roles, system))

    fake_llm.script(guesses())
    run(roles, system)

    assert ("public.employees.mgr", "public.employees.emp_id") not in by_link(
        listed(roles, system, min_confidence=0)
    )
