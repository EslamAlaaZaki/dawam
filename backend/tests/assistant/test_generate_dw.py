"""AI generation of the Core and Mart Layers (spec §6.9, story 88): the ``generate_dw_schema``
tool runs as a job that ends in one Change Set. The model is the scripted fake provider, so
handles, possible duplicates, user overrides, tombstones and abort paths are deterministic.
Jobs run inline in tests: the fake model is asked for the job's turns in order."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from dawam.modules.llm import LlmError, Reply
from dawam.modules.warehouse import GenerationService
from dawam.modules.workspaces import WorkspaceService
from tests.assistant.test_chat import ask, conversation, tool
from tests.assistant.test_jobs import jobs_of, submit
from tests.assistant.test_jobs import model as model  # a fixture
from tests.llm.test_roles_budgets import BASE as LLM
from tests.roles import RoleClients
from tests.warehouse.test_staging_generation import add_system, seed

CUSTOMERS = "stg_shop_sales_customers"
ORDERS = "stg_shop_sales_orders"


def dw(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


@pytest.fixture
def staged(roles: RoleClients) -> RoleClients:
    """A Data Warehouse with two Staging Tables: customers and orders."""
    editor = roles.client("editor")
    assert editor.post(dw(roles), json={"target_platform": "postgresql"}).status_code == 201
    system = add_system(roles, "shop")
    seed(
        roles,
        system,
        "sales",
        {
            "customers": [("customer_no", "varchar"), ("name", "varchar"), ("city", "varchar")],
            "orders": [
                ("order_no", "varchar"),
                ("customer_no", "varchar"),
                ("order_date", "date"),
                ("amount", "numeric"),
            ],
        },
    )
    response = editor.post(f"{dw(roles)}/staging/generate")
    assert response.status_code in (200, 201), response.text
    return roles


def generation(roles: RoleClients) -> GenerationService:
    state = roles.app.state
    return GenerationService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=state.services.clock)
    )


def inputs(roles: RoleClients) -> dict[str, Any]:
    return generation(roles).inputs(roles.user("editor"), roles.workspace_id)


def customer(**overrides: Any) -> dict[str, Any]:
    table = {
        "layer": "core",
        "name": "dim_customer",
        "kind": "dimension",
        "scd_type": 2,
        "is_conformed": True,
        "description": "One row per customer version.",
        "columns": [
            {"name": "customer_no", "data_type": {"type": "string", "length": 20}, "role": "nk"},
            {"name": "name", "data_type": {"type": "string", "length": 200}, "role": "attribute"},
            {"name": "city", "data_type": {"type": "string", "length": 100}, "role": "attribute"},
        ],
    }
    return table | overrides


def sales(**overrides: Any) -> dict[str, Any]:
    table = {
        "layer": "core",
        "name": "fact_sales",
        "kind": "fact",
        "grain": "One row per order.",
        "fact_type": "transactional",
        "columns": [
            {
                "name": "customer_key",
                "data_type": {"type": "bigint"},
                "role": "fk",
                "references": "dim_customer",
                "lookup": {
                    "nk_inputs": [f"{CUSTOMERS}.customer_no"],
                    "as_of_input": f"{ORDERS}.order_date",
                },
            },
            {
                "name": "order_date_key",
                "data_type": {"type": "integer"},
                "role": "fk",
                "references": "date",
                "role_name": "order_date",
            },
            {
                "name": "order_no",
                "data_type": {"type": "string", "length": 20},
                "role": "degenerate_dimension",
            },
            {
                "name": "amount",
                "data_type": {"type": "decimal", "precision": 18, "scale": 2},
                "role": "measure",
                "additivity": "additive",
            },
        ],
    }
    return table | overrides


def run(app, services, roles: RoleClients, fake_llm, *answers: list[dict[str, Any]]):
    """A generation job whose model reads the inputs, then answers each of ``answers`` in
    turn (a later one after the tool reported problems)."""
    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema", "r"),)),
        *(
            Reply(tool_calls=(tool("generate_dw_schema", f"g{n}", tables=tables),))
            for n, tables in enumerate(answers)
        ),
        Reply(text="Generated the model."),
    )
    return submit(app, services, roles, task="Generate the DW Schema")


def results(fake_llm) -> list[str]:
    """What each ``generate_dw_schema`` call returned to the model, in order."""
    return [
        m.content or ""
        for m in fake_llm.calls[-1].messages
        if m.role == "tool" and m.tool_call_id is not None
    ]


def change_sets(roles: RoleClients) -> list[dict[str, Any]]:
    """As the editor, who started the job (a Change Set of a private chat is theirs)."""
    response = roles.client("editor").get(f"/api/v1/workspaces/{roles.workspace_id}/change-sets")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def pending(roles: RoleClients) -> dict[str, Any]:
    [change_set] = [c for c in change_sets(roles) if c["status"] == "pending"]
    response = roles.client("editor").get(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{change_set['id']}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def accept(roles: RoleClients, change_set_id: str, item_ids: list[str] | None = None) -> dict:
    body = {} if item_ids is None else {"item_ids": item_ids}
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{change_set_id}/accept", json=body
    )
    assert response.status_code == 200, response.text
    return response.json()


def tables(roles: RoleClients) -> dict[str, dict[str, Any]]:
    response = roles.client("viewer").get(f"{dw(roles)}/tables")
    assert response.status_code == 200, response.text
    out = {}
    for summary in response.json()["items"]:
        if summary["layer"] != "staging":
            detail = roles.client("viewer").get(f"{dw(roles)}/tables/{summary['id']}").json()
            out[f"{summary['layer']}.{summary['name']}"] = detail
    return out


def provenance(roles: RoleClients, table_id: str) -> dict[str, Any]:
    """A deliberate storage-property check: the provenance the API does not show."""
    with roles.app.state.engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "select generation_key, generated, edited_fields from dw_tables where id = :id"
            ),
            {"id": table_id},
        ).one()
    return {"generation_key": row[0], "generated": row[1], "edited_fields": row[2]}


def again(roles: RoleClients, of: str = "dim_customer", **overrides: Any) -> dict[str, Any]:
    """``customer()`` as a regeneration gives it: the table and its columns by handle."""
    existing = next(t for t in inputs(roles)["existing"] if t["name"] == of)
    handles = {c["name"]: c["handle"] for c in existing["columns"]}
    table = customer(handle=existing["handle"])
    table["columns"] = [{**c, "handle": handles[c["name"]]} for c in table["columns"]]
    return table | overrides


def generate_first(app, services, roles, fake_llm) -> dict[str, dict[str, Any]]:
    job = run(app, services, roles, fake_llm, [customer(), sales()])
    assert job.status == "succeeded", job.error
    accept(roles, pending(roles)["change_set"]["id"])
    return tables(roles)


# -- the first generation ------------------------------------------------------------------


def test_the_chat_hands_generation_to_a_job_that_ends_in_one_change_set(staged, model, fake_llm):
    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema"),)),  # the chat
        Reply(tool_calls=(tool("generate_dw_schema", "r"),)),  # the job reads the inputs
        Reply(tool_calls=(tool("generate_dw_schema", "g", tables=[customer(), sales()]),)),
        Reply(text="Generated."),  # the job's summary
        Reply(text="Generation started."),  # the chat's answer
    )
    editor = staged.client("editor")

    events = ask(editor, staged, conversation(editor, staged), "Generate the core layer")

    [(_, started)] = [e for e in events if e[0] == "tool"]
    assert started["name"] == "generate_dw_schema" and started["status"] == "ok"
    [job] = jobs_of(staged)
    assert job["status"] == "succeeded"
    assert job["log"].count("generate_dw_schema ok") == 2
    detail = pending(staged)
    change_set = detail["change_set"]
    assert change_set["origin"] == "ai" and change_set["scope"] == {"kind": "dw_generation"}
    labels = [i["label"] for i in detail["items"]]
    # The date dimension first (there was none), then dimensions, then facts.
    assert labels == ["core.dim_date", "core.dim_customer", "core.fact_sales"]
    fact = detail["items"][2]
    assert len(fact["depends_on"]) == 2  # on the dimension and the date dimension
    assert all(i["operation"] == "create" for i in detail["items"])


def test_the_model_reads_staging_kpis_and_handles_never_ids(app, services, staged, model, fake_llm):
    staged.client("editor").post(
        f"/api/v1/workspaces/{staged.workspace_id}/kpis",
        json={"name": "Net Sales", "definition": "Sales after returns"},
    )
    generate_first(app, services, staged, fake_llm)

    run(app, services, staged, fake_llm, [customer()])

    seen = fake_llm.calls[-2].messages[-1].content or ""  # what the read returned
    for table in tables(staged).values():
        assert table["id"] not in seen  # handles stand for objects
    read = inputs(staged)
    assert {t["name"] for t in read["staging"]} == {CUSTOMERS, ORDERS}
    assert read["date_dimension"]["exists"] is True
    [dim] = [t for t in read["existing"] if t["name"] == "dim_customer"]
    assert dim["generated"] is True and dim["handle"] in seen
    assert "Net Sales" in seen


def test_accepting_creates_kimball_tables_with_keys_scd2_lookups_and_provenance(
    app, services, staged, model, fake_llm
):
    model_tables = generate_first(app, services, staged, fake_llm)

    dim, fact, date = (
        model_tables["core.dim_customer"],
        model_tables["core.fact_sales"],
        model_tables["core.dim_date"],
    )
    roles_of = {c["name"]: c["role"] for c in dim["columns"]}
    assert roles_of["dim_customer_key"] == "sk"  # DAWAM's surrogate key
    assert roles_of["customer_no"] == "nk"
    assert {"scd_valid_from", "scd_valid_to", "scd_current_flag", "row_hash"} <= set(roles_of)
    assert dim["scd_type"] == 2 and dim["is_conformed"] is True
    assert fact["grain"] == "One row per order." and fact["fact_type"] == "transactional"
    columns = {c["name"]: c for c in fact["columns"]}
    assert columns["amount"]["additivity"] == "additive"
    assert columns["customer_key"]["references_table_id"] == dim["id"]
    assert columns["order_date_key"]["references_table_id"] == date["id"]
    assert date["kind"] == "generated"

    mapping = staged.client("viewer").get(f"{dw(staged)}/tables/{fact['id']}/mapping").json()
    [lookup] = [c for c in mapping["columns"] if c["column_name"] == "customer_key"]
    assert lookup["mapping_type"] == "lookup"
    assert lookup["lookup"]["nk_inputs"] == [f"{CUSTOMERS}.customer_no"]

    stored = provenance(staged, dim["id"])
    assert stored["generation_key"] and stored["edited_fields"] == []
    assert stored["generated"]["scd_type"] == 2
    assert stored["generated"]["name"] == "dim_customer"


def test_a_problem_is_reported_to_the_model_which_fixes_it(app, services, staged, model, fake_llm):
    bad = sales(grain=None)
    bad["columns"][3] = {**bad["columns"][3], "additivity": None}

    job = run(app, services, staged, fake_llm, [customer(), bad], [customer(), sales()])

    assert job.status == "succeeded"
    first, second = results(fake_llm)[1:]
    assert '"not_collected"' in first
    assert "grain" in first and "additivity" in first
    assert '"collected"' in second
    assert len(pending(staged)["items"]) == 3


# -- regeneration --------------------------------------------------------------------------


def test_an_ai_rename_through_its_handle_stays_a_rename(app, services, staged, model, fake_llm):
    before = generate_first(app, services, staged, fake_llm)["core.dim_customer"]
    key = provenance(staged, before["id"])["generation_key"]

    run(app, services, staged, fake_llm, [again(staged, name="dim_client")])

    detail = pending(staged)
    assert detail["change_set"]["origin"] == "regeneration"
    [item] = detail["items"]
    assert item["operation"] == "update" and item["object_id"] == before["id"]
    assert item["payload"]["name"] == "dim_client" and item["is_conflict"] is False
    accept(staged, detail["change_set"]["id"])
    after = tables(staged)["core.dim_client"]
    assert after["id"] == before["id"]
    assert provenance(staged, after["id"])["generation_key"] == key


def test_user_overrides_survive_regeneration_unless_accepted_as_conflicts(
    app, services, staged, model, fake_llm
):
    dim = generate_first(app, services, staged, fake_llm)["core.dim_customer"]
    response = staged.client("editor").patch(
        f"{dw(staged)}/tables/{dim['id']}",
        json={"version": dim["version"], "description": "Customers, as the business sees them."},
    )
    assert response.status_code == 200, response.text
    assert provenance(staged, dim["id"])["edited_fields"] == ["description"]

    # The AI repeats what it said before: the user's value stands, nothing is proposed.
    run(app, services, staged, fake_llm, [again(staged)])
    assert not [c for c in change_sets(staged) if c["status"] == "pending"]

    # The AI changes its mind: the override is a conflict; other fields are plain items.
    run(
        app,
        services,
        staged,
        fake_llm,
        [again(staged, description="A buyer.", is_conformed=False)],
    )
    items = pending(staged)["items"]
    [plain] = [i for i in items if not i["is_conflict"]]
    [conflict] = [i for i in items if i["is_conflict"]]
    assert plain["payload"]["is_conformed"] is False and "description" not in plain["payload"]
    assert conflict["payload"]["description"] == "A buyer."

    accept(staged, pending(staged)["change_set"]["id"], [plain["id"]])  # not the conflict
    assert tables(staged)["core.dim_customer"]["description"] == (
        "Customers, as the business sees them."
    )


def test_an_accepted_conflict_takes_the_ai_value_and_ends_the_override(
    app, services, staged, model, fake_llm
):
    dim = generate_first(app, services, staged, fake_llm)["core.dim_customer"]
    staged.client("editor").patch(
        f"{dw(staged)}/tables/{dim['id']}", json={"version": dim["version"], "description": "Mine"}
    )

    run(app, services, staged, fake_llm, [again(staged, description="A buyer.")])
    accept(staged, pending(staged)["change_set"]["id"])

    assert tables(staged)["core.dim_customer"]["description"] == "A buyer."
    stored = provenance(staged, dim["id"])
    assert stored["edited_fields"] == [] and stored["generated"]["description"] == "A buyer."


@pytest.mark.parametrize("handle", [None, "tgarbled"])
def test_a_missing_or_garbled_handle_becomes_a_flagged_possible_duplicate(
    app, services, staged, model, fake_llm, handle
):
    generate_first(app, services, staged, fake_llm)

    run(app, services, staged, fake_llm, [customer(handle=handle)])

    [item] = pending(staged)["items"]
    assert item["operation"] == "create"
    assert item["payload"]["name"] == "dim_customer_2"  # the name is taken
    assert item["payload"]["review_flags"][0]["code"] == "possible_duplicate"
    assert "possible duplicate of core.dim_customer" in item["label"]


def test_similar_columns_under_a_new_name_are_a_possible_duplicate(
    app, services, staged, model, fake_llm
):
    generate_first(app, services, staged, fake_llm)

    run(app, services, staged, fake_llm, [customer(name="dim_buyer")])

    [item] = pending(staged)["items"]
    assert item["payload"]["name"] == "dim_buyer"
    assert "possible duplicate of core.dim_customer" in item["label"]


def test_a_new_column_of_an_existing_table_is_created_with_its_own_key(
    app, services, staged, model, fake_llm
):
    generate_first(app, services, staged, fake_llm)
    read = inputs(staged)
    dim = next(t for t in read["existing"] if t["name"] == "dim_customer")
    columns = [
        {"handle": c["handle"], "name": c["name"], "data_type": c["type"], "role": c["role"]}
        for c in dim["columns"]
        if c["role"] != "sk"
    ]
    segment = {
        "name": "segment",
        "data_type": {"type": "string", "length": 20},
        "role": "attribute",
    }

    run(
        app,
        services,
        staged,
        fake_llm,
        [customer(handle=dim["handle"], columns=[*columns, segment])],
    )

    [item] = pending(staged)["items"]
    assert item["object_type"] == "dw_column" and item["operation"] == "create"
    accept(staged, pending(staged)["change_set"]["id"])
    assert "segment" in {c["name"] for c in tables(staged)["core.dim_customer"]["columns"]}


def test_a_generated_table_a_user_deleted_is_never_proposed_again(
    app, services, staged, model, fake_llm
):
    model_tables = generate_first(app, services, staged, fake_llm)
    for name in ("core.fact_sales", "core.dim_customer"):
        response = staged.client("editor").delete(f"{dw(staged)}/tables/{model_tables[name]['id']}")
        assert response.status_code == 204, response.text

    run(app, services, staged, fake_llm, [customer()])

    assert not [c for c in change_sets(staged) if c["status"] == "pending"]
    assert "deleted_by_user" in results(fake_llm)[1]
    assert "core.dim_customer" in inputs(staged)["deleted_by_users"]


# -- abort paths ---------------------------------------------------------------------------


def test_a_provider_failure_after_generating_leaves_no_change_set(
    app, services, staged, model, fake_llm
):
    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema", "r"),)),
        Reply(tool_calls=(tool("generate_dw_schema", "g", tables=[customer()]),)),
        LlmError("auth", "The provider refused the API key."),
    )

    job = submit(app, services, staged, task="Generate the DW Schema")

    assert job.status == "failed"
    assert "generate_dw_schema ok" in job.log  # the log is kept
    assert change_sets(staged) == []


def test_a_spent_budget_aborts_generation_with_no_change_set(
    app, services, staged, model, fake_llm, admin_client
):
    budget = admin_client.put(
        f"{LLM}/budgets/workspaces/{staged.workspace_id}", json={"monthly_token_budget": 0}
    )
    assert budget.status_code in (200, 204), budget.text

    job = submit(app, services, staged, task="Generate the DW Schema")

    assert job.status == "failed" and "budget" in (job.error or "")
    assert change_sets(staged) == []


def test_a_job_cannot_mix_generation_with_source_changes(app, services, staged, model, fake_llm):
    from tests.assistant.test_jobs import propose, schema_ids

    table, system = schema_ids(staged)
    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema", "g", tables=[customer()]),)),
        Reply(tool_calls=(propose(table, system, "a"),)),
        Reply(text="Done."),
    )

    job = submit(app, services, staged, task="Generate and describe")

    assert job.status == "succeeded"
    assert "one_change_set" in (fake_llm.calls[-1].messages[-1].content or "")
    [change_set] = change_sets(staged)
    assert change_set["scope"] == {"kind": "dw_generation"}


def test_a_viewer_cannot_generate(staged, model, fake_llm):
    fake_llm.script(Reply(tool_calls=(tool("generate_dw_schema"),)), Reply(text="ok"))
    viewer = staged.client("viewer")

    events = ask(viewer, staged, conversation(viewer, staged), "Generate")

    assert [e[1]["status"] for e in events if e[0] == "tool"] == ["refused"]
    assert jobs_of(staged) == []
