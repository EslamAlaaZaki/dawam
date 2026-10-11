"""Regeneration (spec §6.9 "Regeneration", "Manual mapping edits"; stories 98, 98a): once the
DW Schema was generated, DAWAM offers (a ``regeneration`` notification to the owners and
editors) and never runs a regeneration when a mandatory input changes, offers a regeneration
of one table after a manual mapping edit gives it a new input, and never offers anything for
what regeneration or propagation changed. A table's regeneration is limited to that table,
and only a generation's own items apply as the generator's values."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa

from dawam.modules.changesets import ChangeSetService, ProposedItem
from dawam.modules.llm import Reply
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService
from tests.assistant.test_chat import tool
from tests.assistant.test_generate_dw import (
    ORDERS,
    accept,
    again,
    dw,
    generate_first,
    pending,
    provenance,
    results,
    run,
    tables,
)
from tests.assistant.test_generate_dw import staged as staged  # a fixture
from tests.assistant.test_jobs import model as model  # a fixture
from tests.assistant.test_jobs import submit
from tests.roles import RoleClients


def offers(roles: RoleClients, role: str = "editor") -> list[dict[str, Any]]:
    response = roles.client(role).get("/api/v1/notifications")
    assert response.status_code == 200, response.text
    return [n for n in response.json()["items"] if n["kind"] == "regeneration"]


def read_all(roles: RoleClients) -> None:
    for role in ("owner", "editor"):
        assert roles.client(role).post("/api/v1/notifications/read-all").status_code == 204


def add_kpi(roles: RoleClients, name: str = "Revenue") -> dict[str, Any]:
    response = roles.client("editor").post(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis",
        json={"name": name, "definition": "Sum of order amounts"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def source_table(roles: RoleClients, name: str = "customers") -> tuple[str, str]:
    """The Source System and id of a seeded source table."""
    with roles.app.state.engine.connect() as connection:
        row = connection.execute(
            sa.text(
                "select s.source_system_id, t.id from src_tables t"
                " join src_db_schemas s on s.id = t.db_schema_id where t.name = :n"
            ),
            {"n": name},
        ).one()
    return str(row[0]), str(row[1])


def change_set_engine(roles: RoleClients) -> ChangeSetService:
    state = roles.app.state
    clock = state.services.clock
    return ChangeSetService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock),
        handlers=state.change_set_handlers,
        notifications=NotificationService(state.engine, clock=clock),
        clock=clock,
    )


def propose(roles: RoleClients, origin: str, item: ProposedItem) -> str:
    detail = change_set_engine(roles).propose(
        roles.user("editor"),
        roles.workspace_id,
        origin=origin,
        scope={"kind": f"test_{origin}"},
        title="A test Change Set",
        items=[item],
    )
    return str(detail.change_set.id)


# -- when DAWAM offers a regeneration ----------------------------------------------------


def test_nothing_is_offered_before_the_first_generation_or_by_a_generation(
    app, services, staged, model, fake_llm
):
    add_kpi(staged)  # a mandatory input, but nothing was generated yet
    assert offers(staged) == []

    generate_first(app, services, staged, fake_llm)  # creates tables and lookup mappings
    run(app, services, staged, fake_llm, [again(staged, name="dim_client")])
    accept(staged, pending(staged)["change_set"]["id"])  # a regeneration

    assert offers(staged) == [] and offers(staged, "owner") == []


def test_a_kpi_change_offers_a_regeneration_to_owners_and_editors_once(
    app, services, staged, model, fake_llm
):
    generate_first(app, services, staged, fake_llm)

    kpi = add_kpi(staged)

    [offer] = offers(staged)
    assert offer["ref_type"] == "data_warehouse" and "KPI changes" in offer["message"]
    assert len(offers(staged, "owner")) == 1 and offers(staged, "viewer") == []
    add_kpi(staged, "Order count")  # the offer is still unread: no second one
    assert len(offers(staged)) == 1

    read_all(staged)
    path = f"/api/v1/workspaces/{staged.workspace_id}/kpis/{kpi['id']}"
    response = staged.client("editor").patch(
        path, json={"version": kpi["version"], "owner": "Finance"}
    )
    assert response.status_code == 200, response.text
    assert offers(staged) == []  # the generator does not read a KPI's owner

    response = staged.client("editor").patch(
        path, json={"version": response.json()["version"], "definition": "Net of returns"}
    )
    assert response.status_code == 200, response.text
    assert len(offers(staged)) == 1


def test_a_source_schema_enhancement_offers_a_regeneration(app, services, staged, model, fake_llm):
    generate_first(app, services, staged, fake_llm)
    system_id, table_id = source_table(staged)
    path = f"/api/v1/workspaces/{staged.workspace_id}/systems/{system_id}/tables/{table_id}"

    response = staged.client("editor").patch(path, json={"version": 1, "description": "Buyers"})
    assert response.status_code == 200, response.text
    assert offers(staged) == []  # a description is not a mandatory input

    response = staged.client("editor").patch(
        path, json={"version": response.json()["version"], "classification": "master"}
    )
    assert response.status_code == 200, response.text
    [offer] = offers(staged)
    assert "Source Schema enhancements" in offer["message"]


def test_a_new_snapshot_offers_a_regeneration(app, services, staged, model, fake_llm):
    generate_first(app, services, staged, fake_llm)
    system_id, _ = source_table(staged)

    staged.app.state.on_snapshot_created(
        staged.workspace_id, uuid.UUID(system_id), staged.user("editor").id
    )

    [offer] = offers(staged)
    assert "a new Snapshot" in offer["message"]


def test_what_propagation_changes_never_offers_a_regeneration(
    app, services, staged, model, fake_llm
):
    generate_first(app, services, staged, fake_llm)
    _, table_id = source_table(staged)

    def classify(origin: str, value: str) -> str:
        return propose(
            staged,
            origin,
            ProposedItem(
                key="t",
                object_type="source_table",
                operation="update",
                object_id=uuid.UUID(table_id),
                payload={"classification": value},
            ),
        )

    accept(staged, classify("propagation", "master"))
    assert offers(staged) == []

    accept(staged, classify("ai", "reference"))  # accepted from the assistant: an input change
    assert len(offers(staged)) == 1


# -- a manual mapping edit -----------------------------------------------------------------


def test_a_mapping_edit_that_adds_an_input_offers_to_regenerate_only_that_table(
    app, services, staged, model, fake_llm
):
    fact = generate_first(app, services, staged, fake_llm)["core.fact_sales"]
    columns = {c["name"]: c for c in fact["columns"]}
    mapping = f"{dw(staged)}/tables/{fact['id']}/mapping/columns"

    def save(column: str, **body: Any) -> None:
        response = staged.client("editor").put(f"{mapping}/{columns[column]['id']}", json=body)
        assert response.status_code == 200, response.text

    save("amount", mapping_type="direct", sql_expression=f"{ORDERS}.amount")

    [offer] = offers(staged)
    assert (offer["ref_type"], offer["ref_id"]) == ("dw_table", fact["id"])
    assert "core.fact_sales" in offer["message"]
    assert len(offers(staged, "owner")) == 1

    read_all(staged)
    save("amount", mapping_type="derived", sql_expression=f"{ORDERS}.amount * 1")
    assert offers(staged) == []  # the same inputs

    save("order_no", mapping_type="direct", sql_expression=f"{ORDERS}.order_no")
    assert len(offers(staged)) == 1
    # Offered, never run: nothing was proposed.
    assert not [
        c
        for c in staged.client("editor")
        .get(f"/api/v1/workspaces/{staged.workspace_id}/change-sets")
        .json()["items"]
        if c["status"] == "pending"
    ]


# -- a table's regeneration ------------------------------------------------------------------


def run_scoped(app, services, roles: RoleClients, fake_llm, table: str, *answers):
    fake_llm.script(
        Reply(tool_calls=(tool("generate_dw_schema", "r", table=table),)),
        *(
            Reply(tool_calls=(tool("generate_dw_schema", f"g{n}", table=table, tables=t),))
            for n, t in enumerate(answers)
        ),
        Reply(text="Regenerated the table."),
    )
    return submit(app, services, roles, task=f"Regenerate {table}")


def test_a_table_regeneration_changes_only_that_table(app, services, staged, model, fake_llm):
    dim = generate_first(app, services, staged, fake_llm)["core.dim_customer"]

    job = run_scoped(
        app,
        services,
        staged,
        fake_llm,
        "core.dim_customer",
        [again(staged, name="dim_client"), again(staged, description="A buyer.")],
        [again(staged, description="A buyer.")],
    )

    assert job.status == "succeeded", job.error
    read, refused, _ = results(fake_llm)
    assert "regenerate_only" in read and "mapping_inputs" in read
    assert "not_collected" in refused and "limited to core.dim_customer" in refused
    detail = pending(staged)
    assert detail["change_set"]["scope"] == {"kind": "dw_generation", "table_id": dim["id"]}
    assert {i["object_id"] for i in detail["items"]} == {dim["id"]}


# -- provenance --------------------------------------------------------------------------------


def test_any_other_change_set_item_is_a_user_edit_whatever_it_claims(
    app, services, staged, model, fake_llm
):
    dim = generate_first(app, services, staged, fake_llm)["core.dim_customer"]

    accept(
        staged,
        propose(
            staged,
            "ai",
            ProposedItem(
                key="d",
                object_type="dw_table",
                operation="update",
                object_id=uuid.UUID(dim["id"]),
                payload={"description": "From the chat.", "provenance": "generation"},
            ),
        ),
    )

    stored = provenance(staged, dim["id"])
    assert stored["edited_fields"] == ["description"]
    assert stored["generated"]["description"] != "From the chat."
    assert tables(staged)["core.dim_customer"]["description"] == "From the chat."
