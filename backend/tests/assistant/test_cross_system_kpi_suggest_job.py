"""Suggest cross-system KPIs for the Data Warehouse as a background job (spec §6.13, story 75).

It may start only once the Workspace has two or more Source Systems (deleted ones not
counted) or a DW model (Core or Mart has tables). The server pins the scope to the Data
Warehouse; a suggestion must be based on tables of two or more Source Systems or on DW model
tables, or it is dropped and counted; once the DW model exists suggestions carry validated
formula SQL and links. The model sees every KPI in the Workspace. Jobs run inline in tests
and the model is the scripted fake provider.
"""

from __future__ import annotations

import uuid
from typing import Any

from dawam.modules.assistant import KPI_SUGGEST_JOB
from dawam.modules.jobs import InlineJobRunner, JobService, QueuedJobRunner
from dawam.modules.kpis import KpiService, KpiSuggestion
from dawam.modules.llm import FakeAdapter, Reply
from dawam.modules.sources import EXTRACT_JOB, SnapshotService, SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from tests.assistant import test_chat
from tests.assistant.test_chat import tool
from tests.authz.matrix import _OneTableSource, snapshot_id, system_id
from tests.kpis.test_kpi_links import column, dw, table
from tests.roles import RoleClients

model = test_chat.model
settings = test_chat.settings

CBS = "cbs.public.accounts"
CRM = "crm.public.accounts"


def path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse/kpi-suggestions"


def systems_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/systems"


def crm(roles: RoleClients, *, extracted: bool = False) -> str:
    """A second Source System, CRM (``crm``), with a Snapshot of ``public.accounts`` if
    ``extracted``; Core Banking (``cbs``) is extracted first."""
    snapshot_id(roles)
    response = roles.client("owner").post(systems_path(roles), json={"name": "CRM", "code": "crm"})
    assert response.status_code == 201, response.text
    system = response.json()["id"]
    if extracted:
        owner = roles.client("owner")
        connection = owner.put(
            f"{systems_path(roles)}/{system}/connection",
            json={
                "host": "127.0.0.1",
                "port": 1,
                "database": "crm",
                "username": "reader",
                "allowed_schemas": ["public"],
            },
        )
        assert connection.status_code in (200, 201), connection.text
        state = roles.app.state
        runner = InlineJobRunner()
        snapshots = SnapshotService(
            state.engine,
            workspaces=WorkspaceService(state.engine, clock=state.services.clock),
            jobs=runner,
            encryption_key=state.settings.encryption_key.get_secret_value(),
            clock=state.services.clock,
            connectors=lambda engine, params: _OneTableSource(),  # type: ignore[arg-type,return-value]
        )
        runner.register(EXTRACT_JOB, snapshots.run_extraction)
        snapshots.start_extraction(roles.user("owner"), roles.workspace_id, uuid.UUID(system))
    return system


def dw_model(roles: RoleClients) -> dict[str, Any]:
    """Core ``fact_loan`` with the measure ``amount``."""
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    fact = table(roles, "fact_loan", "core", "fact")
    return {"amount": column(roles, fact["id"], "amount", "measure", additivity="additive")}


def idea(name: str, *based_on: str, sql: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "rationale": "Accounts in both systems match on the customer key.",
        "based_on": list(based_on),
        "formula_sql": sql,
    }


def run(roles: RoleClients, role: str = "editor") -> dict[str, Any]:
    """Start the job (it runs inline) and read its outcome."""
    client = roles.client(role)
    started = client.post(path(roles))
    assert started.status_code == 202, started.text
    latest = client.get(path(roles))
    assert latest.status_code == 200, latest.text
    return latest.json()["job"]


def suggest(roles: RoleClients, fake_llm: FakeAdapter, *ideas: dict[str, Any]) -> dict[str, Any]:
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(tool_calls=(tool("suggest_kpis", suggestions=list(ideas)),)),
        Reply(text="Suggested."),
    )
    return run(roles)


def dw_kpis(roles: RoleClients) -> dict[str, dict[str, Any]]:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis",
        params={"review": "all", "data_warehouse": "true"},
    )
    assert response.status_code == 200, response.text
    return {k["name"]: k for k in response.json()["items"]}


def test_it_is_unavailable_until_there_are_two_source_systems_or_a_dw_model(roles, model):
    snapshot_id(roles)
    editor = roles.client("editor")

    shown = editor.get(path(roles)).json()
    refused = editor.post(path(roles))

    assert shown == {"job": None, "available": False, "source_systems": 1, "dw_model": False}
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "kpi_suggestions_unavailable"
    second = crm(roles)
    assert editor.get(path(roles)).json()["available"] is True
    # a deleted Source System does not count
    assert roles.client("owner").delete(f"{systems_path(roles)}/{second}").status_code == 204
    assert editor.get(path(roles)).json()["source_systems"] == 1
    assert editor.post(path(roles)).status_code == 409


def test_a_dw_model_alone_makes_it_available(roles, model, fake_llm: FakeAdapter):
    system_id(roles)
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    # a set-up Data Warehouse without Core or Mart tables is no DW model yet
    assert roles.client("editor").get(path(roles)).json()["dw_model"] is False
    table(roles, "fact_loan", "core", "fact")

    shown = roles.client("editor").get(path(roles)).json()

    assert (shown["available"], shown["source_systems"], shown["dw_model"]) == (True, 1, True)
    fake_llm.script(Reply(text="Nothing new."))
    assert run(roles)["status"] == "succeeded"


def test_the_scope_is_pinned_and_one_system_suggestions_are_dropped_and_counted(
    roles, model, fake_llm: FakeAdapter
):
    other = crm(roles, extracted=True)

    # The model names a Source System: the job documents under the Data Warehouse anyway.
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(
            tool_calls=(
                tool(
                    "suggest_kpis",
                    system_id=other,
                    suggestions=[
                        idea("Customers in both", "CBS.public.Accounts", CRM),
                        idea("Only Core Banking", CBS),
                        idea("Unknown table", CBS, "crm.public.nope"),
                        idea("No tables"),
                    ],
                ),
            )
        ),
        Reply(text="Suggested one KPI."),
    )
    job = run(roles)

    assert job["status"] == "succeeded"
    assert (job["created"], job["dropped"], job["skipped"]) == (1, 3, 0)
    [kpi] = dw_kpis(roles).values()
    assert kpi["name"] == "Customers in both" and kpi["source_system_id"] is None
    assert (kpi["origin"], kpi["review_state"]) == ("ai", "suggested")
    assert kpi["based_on"] == [CBS, CRM]
    offered = {t.name: t for t in fake_llm.calls[0].tools}
    assert "system_id" not in offered["suggest_kpis"].parameters["properties"]
    assert {"search_catalog", "get_relationships", "workspace_overview"} <= set(offered)


def test_without_a_dw_model_suggestions_are_words_only(roles, model, fake_llm: FakeAdapter):
    crm(roles, extracted=True)

    suggest(roles, fake_llm, idea("Shared customers", CBS, CRM, sql="SELECT 1"))

    kpi = dw_kpis(roles)["Shared customers"]
    assert kpi["formula_sql"] is None and kpi["link_count"] == 0 and kpi["feasibility"] is None


def test_with_a_dw_model_suggestions_carry_validated_formula_sql_and_links(
    roles, model, fake_llm: FakeAdapter
):
    snapshot_id(roles)
    columns = dw_model(roles)

    job = suggest(
        roles,
        fake_llm,
        idea(
            "Loan volume by customer",
            "core.fact_loan",
            CBS,
            sql="SELECT SUM(amount) FROM fact_loan",
        ),
        idea("Bad SQL", "core.fact_loan", sql="SELECT SUM(nope) FROM fact_loan"),
    )

    assert (job["created"], job["dropped"]) == (2, 0)
    kpis = dw_kpis(roles)
    good = kpis["Loan volume by customer"]
    assert good["formula_sql"] == "SELECT SUM(amount) FROM fact_loan"
    assert good["link_count"] == 1 and good["feasibility"] is not None
    assert good["based_on"] == ["core.fact_loan", CBS]
    links = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis/{good['id']}/links"
    )
    assert [c["dw_column_id"] for c in links.json()["items"]] == [columns["amount"]["id"]]
    assert kpis["Bad SQL"]["formula_sql"] is None and kpis["Bad SQL"]["link_count"] == 0


def test_every_kpi_in_the_workspace_reaches_the_prompt(roles, model, fake_llm: FakeAdapter):
    system = str(system_id(roles))
    crm(roles)
    owner = roles.client("owner")
    base = f"/api/v1/workspaces/{roles.workspace_id}/kpis"
    live = owner.post(
        base, json={"name": "Live KPI", "definition": "Added by hand", "source_system_id": system}
    )
    assert live.status_code == 201, live.text
    owner.post(base, json={"name": "Warehouse KPI"})
    snapshot_id(roles)
    state = roles.app.state
    workspaces = WorkspaceService(state.engine, clock=state.services.clock)
    KpiService(
        state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(
            state.engine, workspaces=workspaces, clock=state.services.clock
        ),
        clock=state.services.clock,
    ).suggest(
        roles.user("owner"),
        roles.workspace_id,
        [KpiSuggestion(name="Rejected KPI", rationale="r", based_on=("public.accounts",))],
        source_system_id=uuid.UUID(system),
    )
    rejected = next(
        k
        for k in owner.get(base, params={"review": "all"}).json()["items"]
        if k["name"] == "Rejected KPI"
    )
    assert owner.post(f"{base}/{rejected['id']}/reject").status_code == 200
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="Nothing new."))

    run(roles)

    task = fake_llm.calls[0].messages[-1].content or ""
    for line in (
        "[live] Live KPI: Added by hand",
        "[live] Warehouse KPI",
        "[rejected] Rejected KPI",
        "cbs (id",
        "crm (id",
        "no DW model yet",
    ):
        assert line in task
    assert "suggest only cross-system" in task.lower() or "only cross-system" in task


def test_one_dw_job_runs_at_a_time(roles, model, fake_llm: FakeAdapter):
    crm(roles)
    queue = QueuedJobRunner()
    queue.register(KPI_SUGGEST_JOB, lambda params, ctx: None)
    state = roles.app.state
    waiting = JobService(state.engine, runner=queue, clock=state.services.clock).submit(
        roles.workspace_id,
        KPI_SUGGEST_JOB,
        {"scope": "data_warehouse"},
        title="Suggest cross-system KPIs",
        created_by=roles.user("editor").id,
    )
    editor = roles.client("editor")

    refused = editor.post(path(roles))

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "kpi_suggestions_running"
    assert editor.get(path(roles)).json()["job"]["job_id"] == str(waiting.id)


def test_viewers_cannot_start_or_read_it(roles, model):
    crm(roles)
    viewer = roles.client("viewer")

    assert viewer.post(path(roles)).status_code == 403
    assert viewer.get(path(roles)).status_code == 403
