"""AI mapping drafts (spec §6.9, story 101): the ``draft_mappings`` tool drafts branches,
column mappings and lookups of Core tables as a Change Set. The model is the scripted fake
provider. Several tables run as a job; one table is drafted in the chat. Jobs run inline in
tests: the fake model is asked for the job's turns in order."""

from __future__ import annotations

import uuid
from typing import Any

from dawam.modules.assistant.internal.tools import Proposals
from dawam.modules.changesets import ProposedItem
from dawam.modules.llm import Reply
from dawam.modules.warehouse import MappingDraftPlan
from tests.assistant.test_chat import ask, conversation, tool
from tests.assistant.test_generate_dw import (
    CUSTOMERS,
    ORDERS,
    accept,
    change_sets,
    dw,
    generate_first,
    pending,
    results,
)
from tests.assistant.test_generate_dw import staged as staged  # a fixture
from tests.assistant.test_jobs import jobs_of
from tests.assistant.test_jobs import model as model


def customer_draft(**overrides: Any) -> dict[str, Any]:
    branch = {
        "name": "Shop",
        "driving_input": CUSTOMERS,
        "columns": [
            {
                "column": "customer_no",
                "mapping_type": "direct",
                "sql_expression": f"{CUSTOMERS}.customer_no",
                "rule_text": "The shop's customer number.",
            },
            {
                "column": "name",
                "mapping_type": "derived",
                "sql_expression": f"TRIM({CUSTOMERS}.name)",
            },
            {"column": "city", "mapping_type": "direct", "sql_expression": f"{CUSTOMERS}.city"},
        ],
    }
    return {"table": "dim_customer", "branches": [branch | overrides]}


def sales_draft() -> dict[str, Any]:
    return {
        "table": "core.fact_sales",
        "branches": [
            {
                "name": "Shop",
                "driving_input": ORDERS,
                "columns": [
                    {
                        "column": "customer_key",
                        "mapping_type": "lookup",
                        "lookup": {
                            "nk_inputs": [f"{CUSTOMERS}.customer_no"],
                            "as_of_input": f"{ORDERS}.order_date",
                        },
                    },
                    {
                        "column": "order_no",
                        "mapping_type": "direct",
                        "sql_expression": f"{ORDERS}.order_no",
                    },
                    {
                        "column": "amount",
                        "mapping_type": "direct",
                        "sql_expression": f"{ORDERS}.amount",
                    },
                ],
            }
        ],
    }


def mapping(roles, table_id: str) -> dict[str, Any]:
    response = roles.client("viewer").get(f"{dw(roles)}/tables/{table_id}/mapping")
    assert response.status_code == 200, response.text
    return response.json()


def generated(app, services, roles, fake_llm) -> dict[str, dict[str, Any]]:
    return generate_first(app, services, roles, fake_llm)


# -- one table, in the chat ----------------------------------------------------------------


def test_one_table_is_drafted_in_the_chat_as_a_change_set(app, services, staged, model, fake_llm):
    core = generated(app, services, staged, fake_llm)
    fake_llm.script(
        Reply(tool_calls=(tool("draft_mappings", "r", tables=["dim_customer"]),)),
        Reply(tool_calls=(tool("draft_mappings", "d", drafts=[customer_draft()]),)),
        Reply(text="Drafted."),
    )
    editor = staged.client("editor")

    events = ask(editor, staged, conversation(editor, staged), "Draft the customer mappings")

    assert [e[1]["status"] for e in events if e[0] == "tool"] == ["ok", "ok"]
    seen = fake_llm.calls[1].messages[-1].content or ""  # what the read returned
    assert CUSTOMERS in seen and core["core.dim_customer"]["id"] not in seen
    detail = pending(staged)
    change_set = detail["change_set"]
    assert change_set["origin"] == "ai" and change_set["scope"]["kind"] == "mapping_draft"
    [item] = detail["items"]
    assert item["label"] == "core.dim_customer: Shop" and item["operation"] == "create"
    assert [c["column_name"] for c in item["payload"]["columns"]] == [
        "customer_no",
        "name",
        "city",
    ]
    # Nothing is stored before the member accepts.
    assert mapping(staged, core["core.dim_customer"]["id"])["branches"] == []

    accept(staged, change_set["id"])

    view = mapping(staged, core["core.dim_customer"]["id"])
    [branch] = view["branches"]
    assert branch["name"] == "Shop" and branch["driving_input"] == CUSTOMERS
    by_name = {c["column_name"]: c for c in branch["columns"]}
    assert by_name["customer_no"]["mapping_type"] == "direct"
    assert by_name["name"]["mapping_type"] == "derived"
    assert by_name["city"]["rule_text"] == ""


def test_a_second_draft_never_overwrites_mapped_columns(app, services, staged, model, fake_llm):
    generated(app, services, staged, fake_llm)
    fake_llm.script(
        Reply(tool_calls=(tool("draft_mappings", "d", drafts=[customer_draft()]),)),
        Reply(text="Drafted."),
        Reply(tool_calls=(tool("draft_mappings", "e", drafts=[customer_draft()]),)),
        Reply(text="Again."),
    )
    editor = staged.client("editor")
    ask(editor, staged, conversation(editor, staged), "Draft the customer mappings")
    accept(staged, pending(staged)["change_set"]["id"])

    ask(editor, staged, conversation(editor, staged), "Draft them again")

    [answer] = [m for m in results(fake_llm) if "nothing_to_draft" in m]
    assert "already_mapped" in answer and "branch_exists" in answer
    assert [c for c in change_sets(staged) if c["status"] == "pending"] == []


def test_problems_are_reported_and_nothing_is_collected(app, services, staged, model, fake_llm):
    generated(app, services, staged, fake_llm)
    bad = customer_draft()
    bad["branches"][0]["columns"][1]["sql_expression"] = f"{CUSTOMERS}.no_such_column"
    bad["branches"][0]["columns"].append(
        {"column": "no_column", "mapping_type": "direct", "sql_expression": f"{CUSTOMERS}.city"}
    )
    fake_llm.script(
        Reply(tool_calls=(tool("draft_mappings", "d", drafts=[bad]),)),
        Reply(text="Could not draft."),
    )
    editor = staged.client("editor")

    ask(editor, staged, conversation(editor, staged), "Draft the customer mappings")

    [answer] = results(fake_llm)
    assert "not_collected" in answer and "no_such_column" in answer and "no_column" in answer
    # Only the (applied) generation's Change Set exists.
    assert [c["scope"]["kind"] for c in change_sets(staged)] == ["dw_generation"]


# -- several tables, as a job --------------------------------------------------------------


def test_several_tables_run_as_a_job_that_ends_in_one_change_set(
    app, services, staged, model, fake_llm
):
    core = generated(app, services, staged, fake_llm)
    fake_llm.script(
        Reply(tool_calls=(tool("draft_mappings", "c", tables=["dim_customer", "fact_sales"]),)),
        Reply(tool_calls=(tool("draft_mappings", "r", tables=["dim_customer", "fact_sales"]),)),
        Reply(tool_calls=(tool("draft_mappings", "d", drafts=[customer_draft(), sales_draft()]),)),
        Reply(text="Drafted both."),  # the job's summary
        Reply(text="Drafting started."),  # the chat's answer
    )
    editor = staged.client("editor")

    events = ask(editor, staged, conversation(editor, staged), "Draft all the mappings")

    [(_, started)] = [e for e in events if e[0] == "tool"]
    assert started["name"] == "draft_mappings" and started["status"] == "ok"
    [job] = [j for j in jobs_of(staged) if j["title"] == "Draft mappings"]  # not the generation's
    assert job["status"] == "succeeded"
    assert job["log"].count("draft_mappings ok") == 2
    detail = pending(staged)
    assert detail["change_set"]["scope"]["kind"] == "mapping_draft"
    assert [i["label"] for i in detail["items"]] == [
        "core.dim_customer: Shop",
        "core.fact_sales: Shop",
    ]

    accept(staged, detail["change_set"]["id"])

    [branch] = mapping(staged, core["core.fact_sales"]["id"])["branches"]
    by_name = {c["column_name"]: c for c in branch["columns"]}
    lookup = by_name["customer_key"]
    assert lookup["mapping_type"] == "lookup"
    assert lookup["lookup"]["nk_inputs"] == [f"{CUSTOMERS}.customer_no"]
    assert lookup["lookup"]["dimension_name"] == "dim_customer"
    assert by_name["amount"]["sql_expression"] == f"{ORDERS}.amount"
    lineage = staged.client("viewer").get(
        f"{dw(staged)}/lineage/columns/{by_name['amount']['column_id']}"
    )
    assert lineage.status_code == 200, lineage.text
    # Upstream also reaches the staging mappings; the one edge into the column is the draft's.
    [edge] = [
        e
        for e in lineage.json()["edges"]
        if e["kind"] == "value" and e["to_id"] == by_name["amount"]["column_id"]
    ]
    assert edge["from_label"].endswith("orders.amount")


# -- the job's collection, without a database -----------------------------------------------


def item(table_id: uuid.UUID, key: str = "map0") -> ProposedItem:
    return ProposedItem(
        key=key,
        object_type="mapping_draft",
        operation="create",
        payload={"table_id": str(table_id), "branch": {}, "columns": []},
    )


def test_drafting_a_table_again_replaces_its_earlier_items():
    one, two = uuid.uuid4(), uuid.uuid4()
    proposals = Proposals()
    proposals.add_mapping_draft(MappingDraftPlan([item(one), item(two, "map1")], [one, two]))

    total = proposals.add_mapping_draft(MappingDraftPlan([item(one)], [one]))

    plan = proposals.mapping_draft()
    assert total == 2
    assert [i.payload["table_id"] for i in plan.items] == [str(two), str(one)]
    assert [i.key for i in plan.items] == ["map0", "map1"]  # keys stay unique
    assert plan.scope == {"kind": "mapping_draft", "table_ids": sorted([str(one), str(two)])}


def test_a_job_does_not_mix_mapping_drafts_with_other_changes():
    table = uuid.uuid4()
    proposals = Proposals()
    proposals.add_mapping_draft(MappingDraftPlan([item(table)], [table]))

    try:
        proposals.check(uuid.uuid4(), [item(table, "other")])
    except Exception as exc:
        assert getattr(exc, "code", "") == "one_change_set"
    else:  # pragma: no cover
        raise AssertionError("a mix was allowed")
