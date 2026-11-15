"""AI generation of the Core and Mart Layers (spec §6.9, story 88): the ``generate_dw_schema``
tool runs as a job that ends in one Change Set. The model is the scripted fake provider, so
handles, possible duplicates, user overrides, tombstones and abort paths are deterministic.
Jobs run inline in tests: the fake model is asked for the job's turns in order."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.assistant.internal.tools import _generated_table, _GenTable
from dawam.modules.changesets.api import change_set_service
from dawam.modules.jobs import JobService
from dawam.modules.llm import LlmError, Reply
from dawam.modules.warehouse import GenerationService
from dawam.modules.warehouse.generation import DwTableHandler, GenerationPlan, _Planner
from dawam.modules.warehouse.score_trigger import ScoreScheduler
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.errors import ApiError
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
    job = submit(app, services, roles, task="Generate the DW Schema")
    return approve_all(app, services, roles, job)


def approve_all(app, services, roles: RoleClients, job):
    """Regenerating an existing Layer asks the member first: say yes until the job is done."""
    queue = JobService(app.state.engine, runner=services.jobs, clock=services.clock)
    while job.status == "paused":
        response = roles.client("owner").post(
            f"/api/v1/jobs/{job.id}/answer", json={"answer": "yes"}
        )
        assert response.status_code == 200, response.text
        job = queue.get(roles.user("owner"), job.id)
    return job


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
    # One fact references it: not conformed, whatever the model said.
    assert dim["scd_type"] == 2 and dim["is_conformed"] is False
    assert fact["grain"] == "One row per order." and fact["fact_type"] == "transactional"
    columns = {c["name"]: c for c in fact["columns"]}
    assert columns["amount"]["additivity"] == "additive"
    # DAWAM names foreign keys after the keys they point to.
    assert columns["dim_customer_key"]["references_table_id"] == dim["id"]
    assert columns["order_date_date_key"]["references_table_id"] == date["id"]
    assert date["kind"] == "generated"

    mapping = staged.client("viewer").get(f"{dw(staged)}/tables/{fact['id']}/mapping").json()
    [lookup] = [c for c in mapping["columns"] if c["column_name"] == "dim_customer_key"]
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


def test_a_new_table_never_takes_the_name_a_rename_gives(app, services, staged, model, fake_llm):
    generate_first(app, services, staged, fake_llm)

    run(
        app,
        services,
        staged,
        fake_llm,
        [again(staged, name="dim_client"), customer(name="dim_client")],
    )

    detail = pending(staged)
    [create] = [i for i in detail["items"] if i["operation"] == "create"]
    assert create["payload"]["name"] == "dim_client_2"
    accept(staged, detail["change_set"]["id"])
    assert {"core.dim_client", "core.dim_client_2"} <= set(tables(staged))


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
        [again(staged, description="A buyer.", scd_type=1)],
    )
    items = pending(staged)["items"]
    [plain] = [i for i in items if not i["is_conflict"]]
    [conflict] = [i for i in items if i["is_conflict"]]
    assert plain["payload"]["scd_type"] == 1 and "description" not in plain["payload"]
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
        response = staged.client("owner").delete(f"{dw(staged)}/tables/{model_tables[name]['id']}")
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


def test_regenerating_an_existing_layer_pauses_but_the_first_generation_does_not(
    app, services, staged, model, fake_llm
):
    first = run(app, services, staged, fake_llm, [customer(), sales()])
    assert first.status == "succeeded"  # no Core or Mart Layer yet: no question
    accept(staged, pending(staged)["change_set"]["id"])

    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema", "g", tables=[again(staged)]),)),
        Reply(text="Done."),
    )
    job = submit(app, services, staged, task="Generate the DW Schema")

    [paused] = [j for j in jobs_of(staged) if j["id"] == str(job.id)]
    assert paused["status"] == "paused" and "regenerate" in paused["question"]
    assert approve_all(app, services, staged, job).status == "succeeded"


# -- a plan that is collected always applies (#617) ---------------------------------------


def plan(roles: RoleClients, model_tables: list[dict[str, Any]], **kwargs: Any) -> GenerationPlan:
    """Planning as the tool does it, without a job."""
    return generation(roles).plan(
        roles.user("editor"),
        roles.workspace_id,
        [_generated_table(_GenTable.model_validate(t)) for t in model_tables],
        **kwargs,
    )


def problems(roles: RoleClients, model_tables: list[dict[str, Any]]) -> list[str]:
    with pytest.raises(ApiError) as caught:
        plan(roles, model_tables)
    assert caught.value.code == "invalid_generation"
    return caught.value.details["problems"]


def apply_plan(roles: RoleClients, planned: GenerationPlan) -> None:
    """Propose the plan as its Change Set and accept every item, for real."""
    service = change_set_service(SimpleNamespace(app=roles.app))  # type: ignore[arg-type]
    detail = service.propose(
        roles.user("editor"),
        roles.workspace_id,
        origin=planned.origin,
        scope=planned.scope,
        title="Generate",
        items=planned.items,
    )
    result = accept(roles, str(detail.change_set.id))
    assert result["skipped"] == [] and len(result["accepted"]) == len(planned.items)


def column_of(name: str, role: str = "attribute", **extra: Any) -> dict[str, Any]:
    return {"name": name, "data_type": {"type": "string", "length": 20}, "role": role} | extra


def mart_sales(**overrides: Any) -> dict[str, Any]:
    table = {
        "layer": "mart",
        "name": "fact_daily_sales",
        "kind": "fact",
        "grain": "One row per customer per day.",
        "fact_type": "periodic_snapshot",
        "is_aggregate": True,
        "columns": [
            {
                "name": "customer_key",
                "data_type": {"type": "bigint"},
                "role": "fk",
                "references": "core.dim_customer",
            },
            {
                "name": "total",
                "data_type": {"type": "decimal", "precision": 18, "scale": 2},
                "role": "measure",
                "additivity": "additive",
            },
        ],
    }
    return table | overrides


def with_columns(table: dict[str, Any], *columns: dict[str, Any]) -> dict[str, Any]:
    return table | {"columns": [*table["columns"], *columns]}


def has_problem(found: list[str], where: str, message: str) -> bool:
    return any(p.startswith(f"{where}: ") and message in p for p in found)


@pytest.mark.parametrize(
    ("table", "where", "message"),
    [
        (  # 1: a measure on a dimension
            with_columns(customer(), column_of("budget", "measure", additivity="additive")),
            "core.dim_customer.budget",
            "Only a fact has a measure column.",
        ),
        (  # 3: a degenerate dimension on a dimension
            with_columns(customer(), column_of("ticket_no", "degenerate_dimension")),
            "core.dim_customer.ticket_no",
            "Only a fact has a degenerate dimension column.",
        ),
        (  # 4: an additivity on a non-measure
            with_columns(customer(), column_of("segment", additivity="additive")),
            "core.dim_customer.segment",
            "Only a measure has an additivity.",
        ),
        (  # 5: an SCD type override off a dimension's attribute
            with_columns(sales(), column_of("channel", scd_type_override=2)),
            "core.fact_sales.channel",
            "Only a dimension's attributes have an SCD type override.",
        ),
        (  # 6: a role name on a non-FK
            with_columns(customer(), column_of("segment", role_name="main")),
            "core.dim_customer.segment",
            "Only a foreign key has a role name.",
        ),
        (  # 7: the key DAWAM scaffolds
            with_columns(customer(), column_of("dim_customer_key", "nk")),
            "core.dim_customer.dim_customer_key",
            "DAWAM adds a column named dim_customer_key",
        ),
        (  # 8: an SCD2 housekeeping column
            with_columns(customer(), column_of("scd_valid_from")),
            "core.dim_customer.scd_valid_from",
            "DAWAM adds a column named scd_valid_from",
        ),
    ],
)
def test_a_rule_apply_enforces_is_a_problem_naming_the_table_and_column(
    staged, table, where, message
):
    model_tables = [table, sales()] if table["kind"] == "dimension" else [customer(), table]

    found = problems(staged, model_tables)

    assert has_problem(found, where, message), found


def test_two_foreign_keys_to_one_dimension_need_role_names(staged):
    def dates(opened: str | None, resolved: str | None) -> dict[str, Any]:
        return with_columns(
            sales(),
            column_of("opened", "fk", references="date", role_name=opened),
            column_of("resolved", "fk", references="date", role_name=resolved),
        )

    found = problems(staged, [customer(), dates(None, None)])
    assert has_problem(found, "core.fact_sales.resolved", "give each a role name"), found

    planned = plan(staged, [customer(), dates("opened", "resolved")])

    fact = next(i for i in planned.items if i.label == "core.fact_sales")
    names = [c["name"] for c in fact.payload["columns"]]
    assert {"opened_date_key", "resolved_date_key"} <= set(names)
    apply_plan(staged, planned)


def as_is(roles: RoleClients, of: str, **overrides: Any) -> dict[str, Any]:
    """An existing table as a regeneration describes it unchanged: by handle, with its
    columns by handle (DAWAM's own left out)."""
    table = next(t for t in inputs(roles)["existing"] if t["name"] == of)
    spec = {
        k: v
        for k, v in {
            "handle": table["handle"],
            "layer": table["layer"],
            "name": table["name"],
            "kind": table["kind"],
            "description": table.get("description"),
            "grain": table.get("grain"),
            "fact_type": table.get("fact_type"),
            "is_aggregate": table.get("is_aggregate"),
            "scd_type": table.get("scd_type"),
            "columns": [
                {
                    k: v
                    for k, v in {
                        "handle": c["handle"],
                        "name": c["name"],
                        "data_type": c["type"],
                        "role": c["role"],
                        "additivity": c.get("additivity"),
                        "references": c.get("references"),
                        "role_name": c.get("role_name"),
                    }.items()
                    if v is not None
                }
                for c in table["columns"]
                if c["role"] != "sk" or table["kind"] == "fact"
            ],
        }.items()
        if v is not None
    }
    return spec | overrides


def numeric(name: str, role: str = "attribute", **extra: Any) -> dict[str, Any]:
    return {"name": name, "data_type": {"type": "integer"}, "role": role} | extra


def account(**overrides: Any) -> dict[str, Any]:
    return {
        "layer": "core",
        "name": "dim_account",
        "kind": "dimension",
        "columns": [
            column_of("account_no", "nk"),
            column_of("tier", scd_type_override=2),  # SCD2 history on a type-1 dimension
        ],
    } | overrides


def bridge(**overrides: Any) -> dict[str, Any]:
    return {
        "layer": "core",
        "name": "bridge_account_customer",
        "kind": "bridge",
        "columns": [
            numeric("account_key", "fk", references="dim_account"),
            numeric("customer_key", "fk", references="dim_customer"),
            numeric("share_pct"),
        ],
    } | overrides


VARIED = {
    "role-playing dates": lambda: [
        customer(),
        with_columns(
            sales(),
            numeric("opened", "fk", references="date", role_name="opened"),
            numeric("closed", "fk", references="date", role_name="closed"),
        ),
    ],
    "a bridge and an SCD2 override": lambda: [customer(), account(), bridge(), sales()],
    "a Mart fact on a conformed Core dimension": lambda: [
        customer(),
        sales(),
        sales(name="fact_returns"),
        mart_sales(),
    ],
}


@pytest.mark.parametrize("name", list(VARIED))
def test_a_plan_applies_cleanly_through_the_handlers(staged, name):
    planned = plan(staged, VARIED[name]())

    apply_plan(staged, planned)

    assert {i.label.split(" (")[0] for i in planned.items if i.operation == "create"} <= set(
        tables(staged)
    )


def test_a_regeneration_with_update_items_applies_cleanly(staged):
    apply_plan(staged, plan(staged, [customer(), sales()]))
    dim = as_is(staged, "dim_customer", description="Customers, renamed.", name="dim_client")
    dim["columns"].append(column_of("segment"))

    planned = plan(staged, [dim, as_is(staged, "fact_sales"), sales(name="fact_returns")])

    assert {i.operation for i in planned.items} == {"create", "update"}
    apply_plan(staged, planned)
    assert "segment" in {c["name"] for c in tables(staged)["core.dim_client"]["columns"]}


def test_a_rule_nobody_listed_still_stops_the_plan_through_the_dry_run(staged, monkeypatch):
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise ApiError(422, "invalid_model", "The handler refused.", {"column": "city"})

    monkeypatch.setattr(DwTableHandler, "_create", refuse)

    found = problems(staged, [customer()])

    assert found == ["core.dim_customer.city: The handler refused."]


COUNTED = (
    "dw_tables",
    "dw_columns",
    "table_mappings",
    "mapping_branches",
    "column_mappings",
    "lineage_edges",
    "audit_entries",
    "activity_events",
    "score_runs",
)


def counts(roles: RoleClients) -> dict[str, int]:
    """A deliberate storage-property check: planning must write nothing anywhere."""
    with roles.app.state.engine.connect() as connection:
        return {
            t: connection.execute(sa.text(f"select count(*) from {t}")).scalar_one()
            for t in COUNTED
        }


def test_the_dry_run_leaves_no_trace_and_asks_for_no_score(staged, monkeypatch):
    requested: list[Any] = []  # a score asked for now would run later, in the background
    monkeypatch.setattr(ScoreScheduler, "request", lambda _self, w: requested.append(w))
    before = counts(staged)

    plan(staged, [customer(), sales(), mart_sales(), sales(name="fact_returns")])
    plan(staged, [customer(), sales()])  # again, in one job

    assert counts(staged) == before
    assert requested == []


def test_the_dry_run_releases_its_locks_before_planning_goes_on(staged, monkeypatch):
    apply_plan(staged, plan(staged, [customer(), sales()]))
    dry_run = _Planner._dry_run
    locked: list[int] = []

    def then_lock(self: Any, items: Any) -> None:
        dry_run(self, items)
        # Planning's session is still open: the rows the dry run touched must be free.
        with staged.app.state.engine.connect() as connection:
            for table in ("data_warehouses", "dw_tables", "dw_columns"):
                rows = connection.execute(sa.text(f"select id from {table} for update nowait"))
                locked.append(len(rows.all()))

    monkeypatch.setattr(_Planner, "_dry_run", then_lock)

    plan(staged, [again(staged, description="Regenerated."), as_is(staged, "fact_sales")])

    assert len(locked) == 3 and all(locked)


def test_the_dry_run_runs_no_commit_hook(staged):
    """A released savepoint runs the session's commit hooks, and the score trigger then
    waited forever, from another connection, on the rows the dry run held."""
    released: list[Session] = []

    def on_commit(session: Session) -> None:
        if session.in_nested_transaction():
            released.append(session)
            raise AssertionError("the dry run released a savepoint")

    sa.event.listen(Session, "before_commit", on_commit)
    try:
        plan(staged, [customer(), sales(), mart_sales(), sales(name="fact_returns")])
    finally:
        sa.event.remove(Session, "before_commit", on_commit)

    assert released == []


def test_new_core_and_mart_tables_get_dawams_audit_columns(staged):
    apply_plan(
        staged, plan(staged, [customer(), sales(), sales(name="fact_returns"), mart_sales()])
    )

    for name in ("core.dim_customer", "core.fact_sales", "mart.fact_daily_sales"):
        audit = [
            (c["name"], c["is_system"])
            for c in tables(staged)[name]["columns"]
            if c["role"] == "audit"
        ]
        assert audit == [("dw_created_ts", True), ("dw_updated_ts", True), ("source_system", True)]


def test_audit_column_names_follow_the_naming_rules(staged):
    current = staged.client("editor").get(dw(staged)).json()
    rules = current["naming_rules"] | {
        "created_ts_column": "etl_created",
        "updated_ts_column": "etl_updated",
        "source_system_column": "etl_source",
    }
    response = staged.client("editor").patch(
        dw(staged), json={"version": current["version"], "naming_rules": rules}
    )
    assert response.status_code == 200, response.text

    apply_plan(staged, plan(staged, [customer()]))

    columns = tables(staged)["core.dim_customer"]["columns"]
    assert [c["name"] for c in columns if c["role"] == "audit"] == [
        "etl_created",
        "etl_updated",
        "etl_source",
    ]


def test_a_warehouse_saved_before_the_new_rules_uses_their_defaults(staged):
    with staged.app.state.engine.begin() as connection:  # as stored before the change
        connection.execute(
            sa.text(
                "update data_warehouses set naming_rules = naming_rules::jsonb "
                "- 'created_ts_column' - 'updated_ts_column' where workspace_id = :w"
            ),
            {"w": staged.workspace_id},
        )

    read = staged.client("viewer").get(dw(staged)).json()["naming_rules"]
    assert read["created_ts_column"] == "dw_created_ts"
    assert read["updated_ts_column"] == "dw_updated_ts"
    assert inputs(staged)["dawam_adds"]["every_core_and_mart_table"] == [
        "dw_created_ts",
        "dw_updated_ts",
        "source_system",
    ]
    apply_plan(staged, plan(staged, [customer()]))
    columns = tables(staged)["core.dim_customer"]["columns"]
    assert "dw_created_ts" in {c["name"] for c in columns}


def test_an_audit_column_the_model_adds_is_a_problem(staged):
    found = problems(
        staged,
        [with_columns(customer(), column_of("loaded_at", "audit")), sales()],
    )

    assert has_problem(found, "core.dim_customer.loaded_at", "DAWAM adds the audit columns"), found


def test_dawam_names_foreign_keys_after_the_keys_they_point_to(staged):
    long_role = "r" * 60
    fact = with_columns(
        sales(),
        column_of("long_one", "fk", references="date", role_name=long_role),
    )

    planned = plan(staged, [customer(), fact])

    payload = next(i for i in planned.items if i.label == "core.fact_sales").payload
    by_ref = {c["name"]: c for c in payload["columns"]}
    # Plain, to a dimension of the same plan, and with a role name.
    assert {"dim_customer_key", "order_date_date_key"} <= set(by_ref)
    # Over PostgreSQL's 63 bytes: shortened with the stable hash and flagged.
    [long] = [c for c in payload["columns"] if c["name"].startswith("rrr")]
    assert len(long["name"]) == 63 and long["name"][-7] == "_"
    assert long["review_flags"][0]["code"] == "truncated"
    assert any(n.code == "foreign_key_named" for n in planned.notes)
    apply_plan(staged, planned)
    fact_columns = {c["name"]: c for c in tables(staged)["core.fact_sales"]["columns"]}
    assert fact_columns[long["name"]]["review_flags"][0]["code"] == "truncated"


def test_a_foreign_key_name_that_collides_is_a_problem(staged):
    fact = sales()
    fact["columns"] = [column_of("dim_customer_key"), *fact["columns"]]

    found = problems(staged, [customer(), fact])

    assert has_problem(found, "core.fact_sales.customer_key", "DAWAM names this foreign key"), found


def test_is_conformed_is_derived_from_the_references(staged):
    one = plan(staged, [customer(is_conformed=True), sales()])
    two = plan(staged, [customer(is_conformed=False), sales(), sales(name="fact_returns")])

    def flag(planned: GenerationPlan) -> bool:
        return next(i for i in planned.items if i.label == "core.dim_customer").payload[
            "is_conformed"
        ]

    assert flag(one) is False  # one table references it; the model's True is ignored
    assert flag(two) is True  # two tables do; the model's False is ignored


def test_a_core_dimension_only_one_mart_table_references_is_a_problem(staged):
    found = problems(staged, [customer(), mart_sales()])

    assert has_problem(
        found, "mart.fact_daily_sales.customer_key", "reference it from Core as well"
    ), found


def test_a_hand_edited_is_conformed_is_kept(app, services, staged, model, fake_llm):
    dim = generate_first(app, services, staged, fake_llm)["core.dim_customer"]
    response = staged.client("editor").patch(
        f"{dw(staged)}/tables/{dim['id']}", json={"version": dim["version"], "is_conformed": True}
    )
    assert response.status_code == 200, response.text

    planned = plan(staged, [again(staged)])

    assert planned.items == []  # derived False, but the member's True stands
    assert tables(staged)["core.dim_customer"]["is_conformed"] is True


def test_a_core_fact_foreign_key_needs_a_lookup_except_to_the_date_dimension(staged):
    fact = sales()
    fact["columns"][0] = {k: v for k, v in fact["columns"][0].items() if k != "lookup"}

    found = problems(staged, [customer(), fact])

    assert has_problem(found, "core.fact_sales.customer_key", "gives a `lookup`"), found
    assert not any(p.startswith("core.fact_sales.order_date_key") for p in found)


def test_an_existing_hand_made_design_regenerates_and_gains_dawams_audit_columns(staged):
    from tests.warehouse.test_core_mart_model import column, dimension, fact

    dim = dimension(staged, scd_type=1)
    sales_fact = fact(staged)
    with staged.app.state.engine.begin() as connection:  # made before DAWAM added them
        connection.execute(
            sa.text(
                "delete from dw_columns where is_system and role = 'audit' and table_id in (:a, :b)"
            ),
            {"a": dim["id"], "b": sales_fact["id"]},
        )
    for spec in (
        column_of("customer_no", "nk"),
        {"name": "dw_created_ts", "data_type": {"type": "timestamp"}, "role": "audit"},
        {"name": "valid_from", "data_type": {"type": "timestamp"}, "role": "audit"},
    ):
        column(staged, dim["id"], **spec)
    column(
        staged,
        sales_fact["id"],
        name="customer_sk",
        data_type={"type": "bigint"},
        role="fk",
        references_table_id=dim["id"],
    )
    read = {t["name"]: t for t in inputs(staged)["existing"]}
    # The member's own audit column is theirs, not one DAWAM added.
    assert "dw_created_ts" not in read["dim_customer"]["dawam_columns"]
    as_is = [
        {
            k: v
            for k, v in {
                "handle": t["handle"],
                "layer": t["layer"],
                "name": t["name"],
                "kind": t["kind"],
                "grain": t.get("grain"),
                "fact_type": t.get("fact_type"),
                "scd_type": t.get("scd_type"),
                "columns": [
                    {
                        "handle": c["handle"],
                        "name": c["name"],
                        "data_type": c["type"],
                        "role": c["role"],
                        "references": c.get("references"),
                    }
                    for c in t["columns"]
                    if c["role"] != "sk"
                ],
            }.items()
            if v is not None
        }
        for t in read.values()
    ]

    planned = plan(staged, as_is)

    assert {i.key[:2] for i in planned.items} == {"a:"}  # only DAWAM's audit columns
    assert any(
        n.code == "audit_column_kept" and n.object == "core.dim_customer.dw_created_ts"
        for n in planned.notes
    )
    apply_plan(staged, planned)
    after = tables(staged)
    dim_names = [c["name"] for c in after["core.dim_customer"]["columns"]]
    assert dim_names.count("dw_created_ts") == 1 and "valid_from" in dim_names
    assert {"dw_updated_ts", "source_system"} <= set(dim_names)
    fact_names = [c["name"] for c in after["core.fact_sales"]["columns"]]
    assert "customer_sk" in fact_names  # kept by handle: its name stays
    assert {"dw_created_ts", "dw_updated_ts", "source_system"} <= set(fact_names)


def test_the_inputs_list_the_columns_dawam_adds(app, services, staged, model, fake_llm):
    adds = inputs(staged)["dawam_adds"]
    assert adds["dimension"]["key"] == "<table name>_key"
    assert adds["dimension"]["history_columns"] == [
        "scd_valid_from",
        "scd_valid_to",
        "scd_current_flag",
        "row_hash",
    ]
    assert adds["bridge"]["key"] == "<table name>_group_key"
    assert adds["every_core_and_mart_table"] == ["dw_created_ts", "dw_updated_ts", "source_system"]
    assert "foreign_key_names" in adds and "is_conformed" in adds

    generate_first(app, services, staged, fake_llm)

    dim = next(t for t in inputs(staged)["existing"] if t["name"] == "dim_customer")
    assert set(dim["dawam_columns"]) == {
        "dim_customer_key",
        "scd_valid_from",
        "scd_valid_to",
        "scd_current_flag",
        "row_hash",
        "dw_created_ts",
        "dw_updated_ts",
        "source_system",
    }


# -- review round 1 (#617) ----------------------------------------------------------------


def delete_table(roles: RoleClients, name: str) -> None:
    response = roles.client("owner").delete(f"{dw(roles)}/tables/{tables(roles)[name]['id']}")
    assert response.status_code == 204, response.text


def conformed(roles: RoleClients, name: str = "core.dim_customer") -> bool:
    return tables(roles)[name]["is_conformed"]


def region() -> dict[str, Any]:
    return {
        "layer": "mart",
        "name": "dim_region",
        "kind": "dimension",
        "columns": [column_of("region_code", "nk")],
    }


def test_a_dimension_stops_being_conformed_after_the_mart_moves_its_reference(staged):
    apply_plan(staged, plan(staged, [customer(), sales(), mart_sales()]))
    assert conformed(staged)
    mart = as_is(staged, "fact_daily_sales")
    for column in mart["columns"]:
        if column["role"] == "fk":
            column["references"] = "mart.dim_region"  # kept by handle, pointed elsewhere

    planned = plan(
        staged,
        [as_is(staged, "dim_customer"), as_is(staged, "fact_sales"), region(), mart],
    )

    assert planned.items[-1].key.endswith(":unconform")  # after the Mart item
    apply_plan(staged, planned)
    assert conformed(staged) is False


def test_a_deleted_mart_table_does_not_keep_a_dimension_conformed(staged):
    apply_plan(staged, plan(staged, [customer(), sales(), mart_sales()]))
    delete_table(staged, "mart.fact_daily_sales")

    planned = plan(staged, [as_is(staged, "dim_customer"), as_is(staged, "fact_sales")])

    apply_plan(staged, planned)
    assert conformed(staged) is False


def test_a_one_table_regeneration_keeps_the_stored_flag_of_other_dimensions(staged):
    apply_plan(staged, plan(staged, [customer(), sales(), mart_sales()]))
    delete_table(staged, "core.fact_sales")  # derived: no longer conformed; stored: still
    mart = as_is(staged, "fact_daily_sales", description="Daily sales.")

    planned = plan(staged, [mart], table=mart["handle"])

    assert all(i.label.startswith("mart.fact_daily_sales") for i in planned.items)
    apply_plan(staged, planned)
    assert conformed(staged) is True


def test_a_one_table_regeneration_reports_a_dimension_that_is_not_conformed(staged):
    from tests.warehouse.test_core_mart_model import column, fact

    apply_plan(staged, plan(staged, [customer(), sales()]))
    other = fact(staged, name="fact_visits")  # by hand: derived conformed, stored not
    column(
        staged,
        other["id"],
        name="dim_customer_key",
        data_type={"type": "bigint"},
        role="fk",
        references_table_id=tables(staged)["core.dim_customer"]["id"],
    )
    fact(staged, name="fact_m", layer="mart")
    mart = with_columns(
        as_is(staged, "fact_m"), numeric("customer_key", "fk", references="core.dim_customer")
    )

    found = problems_of(staged, [mart], table=mart["handle"])

    assert has_problem(found, "mart.fact_m.customer_key", "reference it from Core as well"), found


def problems_of(roles: RoleClients, model_tables: list[dict[str, Any]], **kwargs: Any) -> list:
    with pytest.raises(ApiError) as caught:
        plan(roles, model_tables, **kwargs)
    assert caught.value.code == "invalid_generation"
    return caught.value.details["problems"]


def test_a_foreign_key_a_user_deleted_does_not_count_as_a_reference(staged):
    apply_plan(staged, plan(staged, [customer(), sales()]))
    fact_table = tables(staged)["core.fact_sales"]
    fk = next(c for c in fact_table["columns"] if c["name"] == "dim_customer_key")
    response = staged.client("editor").delete(
        f"{dw(staged)}/tables/{fact_table['id']}/columns/{fk['id']}"
    )
    assert response.status_code == 204, response.text
    again_sales = with_columns(
        as_is(staged, "fact_sales"),
        numeric("dim_customer_key", "fk", references="dim_customer"),  # tombstoned
    )

    planned = plan(staged, [as_is(staged, "dim_customer"), again_sales, sales(name="fact_returns")])

    assert not any(i.payload.get("is_conformed") for i in planned.items)


def test_a_one_table_regeneration_changes_only_that_table(staged):
    apply_plan(staged, plan(staged, [customer(), sales()]))
    with staged.app.state.engine.begin() as connection:  # saved before DAWAM added them
        connection.execute(sa.text("delete from dw_columns where is_system and role = 'audit'"))
    fact_sales = as_is(staged, "fact_sales")

    planned = plan(staged, [fact_sales], table=fact_sales["handle"])

    fact_id = tables(staged)["core.fact_sales"]["id"]
    assert [i.key for i in planned.items] == [f"a:{fact_id}"]


def test_a_whole_regeneration_recomputes_a_generated_dimensions_flag(staged):
    apply_plan(staged, plan(staged, [customer(), sales()]))
    dim_id = tables(staged)["core.dim_customer"]["id"]

    planned = plan(
        staged,
        [as_is(staged, "dim_customer"), as_is(staged, "fact_sales"), sales(name="fact_returns")],
    )

    [item] = [i for i in planned.items if i.key == f"u:{dim_id}"]
    assert item.payload["is_conformed"] is True and item.is_conflict is False


def test_a_hand_made_dimensions_flag_is_never_changed(staged):
    from tests.warehouse.test_core_mart_model import column, dimension

    dim = dimension(staged)
    column(staged, dim["id"], **column_of("customer_no", "nk"))

    planned = plan(staged, [sales(), sales(name="fact_returns")])  # derived conformed

    assert not any("is_conformed" in i.payload for i in planned.items if i.object_id == dim["id"])
    apply_plan(staged, planned)
    assert conformed(staged) is False


def test_the_built_in_date_dimension_gets_no_audit_columns(staged):
    apply_plan(staged, plan(staged, [customer(), sales()]))  # generation adds it
    by_generation = tables(staged)["core.dim_date"]
    response = staged.client("editor").post(f"{dw(staged)}/tables/generated", json={"kind": "time"})
    assert response.status_code == 201, response.text

    for detail in (by_generation, response.json()):
        assert not [c for c in detail["columns"] if c["role"] == "audit"]


# -- review round 2 (#617) ----------------------------------------------------------------


def moved_away(roles: RoleClients) -> tuple[Any, Any, Any]:
    """A plan whose Mart fact moves its foreign key off Core dim_customer, so the dimension
    stops being conformed; proposed through the real Change Set engine."""
    apply_plan(roles, plan(roles, [customer(), sales(), mart_sales()]))
    mart = as_is(roles, "fact_daily_sales")
    for column in mart["columns"]:
        if column["role"] == "fk":
            column["references"] = "mart.dim_region"
    planned = plan(
        roles, [as_is(roles, "dim_customer"), as_is(roles, "fact_sales"), region(), mart]
    )
    service = change_set_service(SimpleNamespace(app=roles.app))  # type: ignore[arg-type]
    detail = service.propose(
        roles.user("editor"),
        roles.workspace_id,
        origin=planned.origin,
        scope=planned.scope,
        title="Generate",
        items=planned.items,
    )
    unconform = next(i for i in detail.items if i.payload.get("is_conformed") is False)
    moving = next(i for i in detail.items if i.label.startswith("mart.fact_daily_sales."))
    assert moving.id in unconform.depends_on
    return service, detail, (unconform, moving)


def test_accepting_only_the_unconform_item_applies_the_mart_item_it_needs(staged):
    service, detail, (unconform, moving) = moved_away(staged)

    result = service.accept(
        staged.user("editor"), staged.workspace_id, detail.change_set.id, [unconform.id]
    )

    assert {unconform.id, moving.id} <= set(result.accepted)
    assert conformed(staged) is False


def test_rejecting_the_mart_item_rejects_the_unconform_item_too(staged):
    service, detail, (unconform, moving) = moved_away(staged)

    service.reject(staged.user("editor"), staged.workspace_id, detail.change_set.id, [moving.id])
    result = service.accept(staged.user("editor"), staged.workspace_id, detail.change_set.id)

    assert unconform.id not in result.accepted and moving.id not in result.accepted
    assert conformed(staged) is True  # the Mart fact still references it
