"""Suggest KPIs for one Source System as a background job (spec §6.13, story 75).

The server pins the scope: the job's model gets lookup tools for that system only (no
``system_id`` argument it could set) and every KPI under it in every review state; a
suggestion based on a table outside the system is dropped and counted. Zero suggestions is a
normal outcome; one job runs per system at a time; owners and editors only. Jobs run inline
in tests and the model is the scripted fake provider.
"""

from __future__ import annotations

from typing import Any

from dawam.modules.assistant import KPI_SUGGEST_JOB
from dawam.modules.jobs import JobService, QueuedJobRunner
from dawam.modules.kpis import KpiService, KpiSuggestion
from dawam.modules.llm import FakeAdapter, Reply
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from tests.assistant import test_chat
from tests.assistant.test_chat import tool
from tests.authz.matrix import snapshot_id, system_id
from tests.roles import RoleClients

model = test_chat.model
settings = test_chat.settings

TABLE = "public.accounts"
"""The one table of the stand-in source the Source System is extracted from."""


def path(roles: RoleClients, system: Any) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/systems/{system}/kpi-suggestions"


def scoped_system(roles: RoleClients) -> str:
    """The Source System with a Snapshot of ``public.accounts``."""
    snapshot_id(roles)
    return str(system_id(roles))


def other_system(roles: RoleClients) -> str:
    response = roles.client("owner").post(
        f"/api/v1/workspaces/{roles.workspace_id}/systems", json={"name": "CRM", "code": "crm"}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def idea(name: str, *based_on: str) -> dict[str, Any]:
    return {"name": name, "rationale": "The accounts table holds it.", "based_on": list(based_on)}


def run(roles: RoleClients, system: str, role: str = "editor") -> dict[str, Any]:
    """Start the job (it runs inline) and read its outcome."""
    client = roles.client(role)
    started = client.post(path(roles, system))
    assert started.status_code == 202, started.text
    latest = client.get(path(roles, system))
    assert latest.status_code == 200, latest.text
    return latest.json()["job"]


def kpis(roles: RoleClients, system: str) -> list[dict[str, Any]]:
    response = roles.client("owner").get(
        f"/api/v1/workspaces/{roles.workspace_id}/kpis",
        params={"review": "all", "source_system_id": system},
    )
    return response.json()["items"]


def test_the_scope_is_pinned_and_out_of_scope_suggestions_are_dropped_and_counted(
    roles, model, fake_llm: FakeAdapter
):
    system = scoped_system(roles)
    crm = other_system(roles)
    fake_llm.calls.clear()
    suggestions = [
        idea("Accounts opened", "PUBLIC.Accounts"),
        idea("Churn", "crm.customers"),
        idea("Mixed", TABLE, "crm.customers"),
        idea("No tables"),
    ]
    # The model names another system: the job runs suggest_kpis with its own anyway.
    fake_llm.script(
        Reply(tool_calls=(tool("suggest_kpis", suggestions=suggestions, system_id=crm),)),
        Reply(text="Suggested one KPI."),
    )

    job = run(roles, system)

    assert job["status"] == "succeeded"
    assert (job["created"], job["dropped"], job["skipped"]) == (1, 3, 0)
    [kpi] = kpis(roles, system)
    assert kpi["name"] == "Accounts opened" and kpi["review_state"] == "suggested"
    assert kpi["origin"] == "ai" and kpi["based_on"] == [TABLE]
    assert kpis(roles, crm) == []
    offered = {t.name: t for t in fake_llm.calls[0].tools}
    # Profiles and documents are offered too when the Workspace shares them with the AI.
    lookups = {"search_catalog", "get_object", "get_relationships", "suggest_kpis"}
    assert lookups <= set(offered) <= lookups | {"get_profile", "search_documents"}
    assert all("system_id" not in t.parameters["properties"] for t in offered.values())


def test_a_lookup_reads_the_jobs_system_whatever_the_model_names(
    roles, model, fake_llm: FakeAdapter
):
    system = scoped_system(roles)
    crm = other_system(roles)
    fake_llm.calls.clear()
    fake_llm.script(
        Reply(tool_calls=(tool("search_catalog", query="accounts", system_id=crm),)),
        Reply(text="Nothing new."),
    )

    run(roles, system)

    result = fake_llm.calls[1].messages[-1].content or ""
    assert "accounts" in result and system in result


def test_every_kpi_of_the_system_in_every_state_reaches_the_prompt(
    roles, model, fake_llm: FakeAdapter
):
    system = scoped_system(roles)
    owner = roles.client("owner")
    base = f"/api/v1/workspaces/{roles.workspace_id}/kpis"
    live = owner.post(
        base, json={"name": "Live KPI", "definition": "Added by hand", "source_system_id": system}
    )
    assert live.status_code == 201, live.text
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
        [
            KpiSuggestion(name=name, rationale="r", based_on=(TABLE,))
            for name in ("Waiting KPI", "Accepted KPI", "Rejected KPI")
        ],
        source_system_id=system,
    )
    ids = {k["name"]: k["id"] for k in kpis(roles, system)}
    assert owner.post(f"{base}/{ids['Accepted KPI']}/accept").status_code == 200
    assert owner.post(f"{base}/{ids['Rejected KPI']}/reject").status_code == 200
    owner.post(base, json={"name": "Warehouse KPI"})  # another scope: not listed
    fake_llm.calls.clear()
    fake_llm.script(Reply(text="Nothing new."))

    run(roles, system)

    task = fake_llm.calls[0].messages[-1].content or ""
    for line in (
        "[live] Live KPI: Added by hand",
        "[suggested] Waiting KPI",
        "[accepted] Accepted KPI",
        "[rejected] Rejected KPI",
    ):
        assert line in task
    assert "Warehouse KPI" not in task and TABLE in task


def test_zero_suggestions_is_a_normal_outcome(roles, model, fake_llm: FakeAdapter):
    system = scoped_system(roles)
    fake_llm.script(Reply(text="Nothing differs from the KPIs already here."))

    job = run(roles, system)

    assert job["status"] == "succeeded" and job["error"] is None
    assert (job["created"], job["dropped"], job["skipped"]) == (0, 0, 0)


def test_one_job_runs_per_system_at_a_time(roles, model, fake_llm: FakeAdapter):
    system = scoped_system(roles)
    crm = other_system(roles)
    queue = QueuedJobRunner()
    queue.register(KPI_SUGGEST_JOB, lambda params, ctx: None)
    state = roles.app.state
    waiting = JobService(state.engine, runner=queue, clock=state.services.clock).submit(
        roles.workspace_id,
        KPI_SUGGEST_JOB,
        {"source_system_id": system},
        title="Suggest KPIs",
        created_by=roles.user("editor").id,
    )
    editor = roles.client("editor")

    refused = editor.post(path(roles, system))

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "kpi_suggestions_running"
    assert editor.get(path(roles, system)).json()["job"]["job_id"] == str(waiting.id)
    fake_llm.script(Reply(text="Nothing new."))
    assert editor.post(path(roles, crm)).status_code == 202  # another system's may start


def test_viewers_cannot_start_or_read_it(roles, model, fake_llm: FakeAdapter):
    system = scoped_system(roles)
    viewer = roles.client("viewer")

    assert viewer.post(path(roles, system)).status_code == 403
    assert viewer.get(path(roles, system)).status_code == 403
    assert roles.client("owner").get(path(roles, system)).json() == {"job": None}


def test_a_job_that_fails_reports_why_and_counts_nothing(roles, fake_llm: FakeAdapter):
    system = scoped_system(roles)  # no model assigned to the Workspace

    job = run(roles, system)

    assert job["status"] == "failed" and job["error"]
    assert job["created"] is None and kpis(roles, system) == []
