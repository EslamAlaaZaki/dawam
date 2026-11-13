"""The KPI review (spec §6.13, stories 75-77): the AI's suggestions wait in Suggested until
an owner or editor accepts or rejects them; only the KPIs tab (live and accepted KPIs)
counts anywhere else."""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from dawam.modules.audit import AuditService
from dawam.modules.kpis import KpiService, KpiSuggestion, kpi_names, kpis_for_score
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import WorkspaceService
from tests.kpis.test_kpi_links import column, dw, put_links, table
from tests.kpis.test_kpis import a_system, created, error_code, kpis_path
from tests.roles import RoleClients


def service(app: FastAPI) -> KpiService:
    clock = app.state.services.clock
    workspaces = WorkspaceService(app.state.engine, clock=clock)
    return KpiService(
        app.state.engine,
        workspaces=workspaces,
        systems=SourceSystemService(app.state.engine, workspaces=workspaces, clock=clock),
        clock=clock,
    )


def suggested(
    roles: RoleClients, app: FastAPI, *names: str, system_id: str | None = None
) -> list[dict]:
    """The AI's suggestions, as ``suggest_kpis`` makes them."""
    editor = roles.client("editor")  # a member from now on
    result = service(app).suggest(
        roles.user("editor"),
        roles.workspace_id,
        [KpiSuggestion(name=n, rationale="The ledger points to it.") for n in names],
        source_system_id=uuid.UUID(system_id) if system_id else None,
    )
    return [editor.get(f"{kpis_path(roles)}/{k.id}").json() for k in result.created]


def listed(roles: RoleClients, as_role: str = "editor", **query) -> dict[str, dict]:
    response = roles.client(as_role).get(kpis_path(roles), params=query)
    assert response.status_code == 200, response.text
    return {k["name"]: k for k in response.json()["items"]}


def decide(roles: RoleClients, kpi: dict, action: str, as_role: str = "editor"):
    return roles.client(as_role).post(f"{kpis_path(roles)}/{kpi['id']}/{action}")


def audited(app: FastAPI, roles: RoleClients, kpi: dict) -> list:
    return AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
    )


def test_ai_suggestions_arrive_as_suggested_and_wait_outside_the_kpis_tab(roles, app):
    [kpi] = suggested(roles, app, "Churn")

    assert kpi["review_state"] == "suggested"
    assert kpi["origin"] == "ai" and kpi["status"] == "draft"
    assert kpi["decision"] is None and kpi["decided_by"] is None
    assert "Churn" not in listed(roles)
    assert listed(roles, review="all")["Churn"]["review_state"] == "suggested"


def test_a_kpi_added_by_hand_is_live_and_added_by_its_author(roles, clock):
    kpi = created(roles, name="Revenue")

    assert kpi["review_state"] == "live"
    assert kpi["decision"] == "added"
    assert kpi["decided_by"] == str(roles.user("editor").id)
    assert kpi["link_count"] == 0 and kpi["feasibility"] is None


def test_accepting_a_suggestion_puts_it_in_the_kpis_tab(roles, app, clock):
    [kpi] = suggested(roles, app, "Churn")

    response = decide(roles, kpi, "accept")

    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["review_state"] == "accepted"
    assert accepted["decision"] == "accepted"
    assert accepted["decided_by"] == str(roles.user("editor").id)
    assert accepted["decided_at"] == clock().isoformat().replace("+00:00", "Z")
    assert accepted["status"] == "draft"
    assert listed(roles, as_role="viewer")["Churn"]["review_state"] == "accepted"
    [entry] = [e for e in audited(app, roles, kpi) if e.old is not None]
    assert entry.old == {"review_state": "suggested", "removed": False}
    assert entry.new == {"review_state": "accepted", "removed": False}


def test_rejecting_a_suggestion_keeps_it_out_and_accepting_it_after_all_brings_it_in(roles, app):
    [kpi] = suggested(roles, app, "Churn")

    rejected = decide(roles, kpi, "reject")

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["review_state"] == "rejected"
    assert rejected.json()["decision"] == "rejected"
    assert "Churn" not in listed(roles)
    again = decide(roles, kpi, "reject")
    assert again.status_code == 409 and error_code(again) == "invalid_review_state"

    after_all = decide(roles, kpi, "accept")

    assert after_all.status_code == 200, after_all.text
    assert after_all.json()["review_state"] == "accepted"
    assert "Churn" in listed(roles)
    decisions = [e.new for e in audited(app, roles, kpi) if e.old is not None]
    assert sorted(d["review_state"] for d in decisions) == ["accepted", "rejected"]


def test_a_live_kpi_cannot_be_accepted_or_rejected(roles):
    kpi = created(roles, name="Revenue")

    for action in ("accept", "reject"):
        response = decide(roles, kpi, action)
        assert response.status_code == 409 and error_code(response) == "invalid_review_state"


def test_viewers_cannot_decide_or_read_the_review(roles, app):
    [kpi] = suggested(roles, app, "Churn")

    assert decide(roles, kpi, "accept", as_role="viewer").status_code == 403
    assert decide(roles, kpi, "reject", as_role="viewer").status_code == 403
    assert roles.client("viewer").get(kpis_path(roles), params={"review": "all"}).status_code == 403
    assert roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}").status_code == 404
    assert roles.client("owner").get(f"{kpis_path(roles)}/{kpi['id']}").status_code == 200


def test_deleting_an_accepted_suggestion_moves_it_to_rejected_as_removed(roles, app):
    [kpi] = suggested(roles, app, "Churn")
    decide(roles, kpi, "accept")

    response = roles.client("editor").delete(f"{kpis_path(roles)}/{kpi['id']}")

    assert response.status_code == 204
    removed = listed(roles, review="all")["Churn"]
    assert removed["review_state"] == "rejected"
    assert removed["decision"] == "removed"
    assert "Churn" not in listed(roles)
    assert {"review_state": "rejected", "removed": True} in [
        e.new for e in audited(app, roles, kpi)
    ]
    # it never comes back on its own
    assert suggested(roles, app, "churn") == []


def test_deleting_a_kpi_added_by_hand_removes_it_for_good(roles):
    kpi = created(roles, name="Revenue")

    assert roles.client("editor").delete(f"{kpis_path(roles)}/{kpi['id']}").status_code == 204

    assert listed(roles, review="all") == {}


def test_a_suggestion_named_like_any_kpi_of_its_scope_is_skipped_in_every_state(roles, app):
    system = a_system(roles)
    created(roles, name="Revenue", source_system_id=system)
    [rejected] = suggested(roles, app, "Churn", system_id=system)
    decide(roles, rejected, "reject")
    suggested(roles, app, "Cost to income", system_id=system)

    result = service(app).suggest(
        roles.user("editor"),
        roles.workspace_id,
        [
            KpiSuggestion(name=n, rationale="r")
            for n in ("REVENUE", "churn", "cost TO income", "Deposits")
        ],
        source_system_id=uuid.UUID(system),
    )

    assert [k.name for k in result.created] == ["Deposits"]
    assert result.skipped == ["REVENUE", "churn", "cost TO income"]
    # another scope has its own names
    assert [k["name"] for k in suggested(roles, app, "Revenue")] == ["Revenue"]


@pytest.fixture
def warehouse(roles: RoleClients) -> dict:
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    fact = table(roles, "fact_loan", "core", "fact")
    return {"amount": column(roles, fact["id"], "amount", "measure", additivity="additive")}


def test_only_the_kpis_tab_counts_in_the_score_lineage_and_links(roles, app, warehouse):
    live = created(roles, name="Loan volume")
    put_links(roles, live, [warehouse["amount"]["id"]])
    accepted, rejected, waiting = suggested(roles, app, "Accepted", "Rejected", "Waiting")
    decide(roles, accepted, "accept")
    accepted = roles.client("editor").get(f"{kpis_path(roles)}/{accepted['id']}").json()
    put_links(roles, accepted, [warehouse["amount"]["id"]])
    decide(roles, rejected, "reject")
    ids = [uuid.UUID(k["id"]) for k in (live, accepted, rejected, waiting)]

    with Session(app.state.engine) as db:
        assert {k.name for k in kpis_for_score(db, roles.workspace_id)} == {
            "Loan volume",
            "Accepted",
        }
        assert set(kpi_names(db, roles.workspace_id, ids).values()) == {"Loan volume", "Accepted"}
    candidates = service(app).link_candidates(roles.user("editor"), roles.workspace_id)
    assert {k.name for k in candidates.kpis} == {"Loan volume", "Accepted"}
    refused = roles.client("editor").put(
        f"{kpis_path(roles)}/{waiting['id']}/links",
        json={"version": waiting["version"], "dw_column_ids": [warehouse["amount"]["id"]]},
    )
    assert refused.status_code == 409 and error_code(refused) == "invalid_review_state"
    # the list shows the link count and feasibility, without a request per KPI
    assert listed(roles)["Loan volume"]["link_count"] == 1
    assert listed(roles)["Loan volume"]["feasibility"] == "needs_mapping"


def test_removing_an_accepted_kpi_takes_it_out_of_lineage_until_accepted_again(
    roles, app, warehouse
):
    [kpi] = suggested(roles, app, "Accepted")
    kpi = decide(roles, kpi, "accept").json()
    put_links(roles, kpi, [warehouse["amount"]["id"]])

    def kpi_nodes() -> list[str]:
        response = roles.client("viewer").get(
            f"{dw(roles)}/lineage",
            params={"node": warehouse["amount"]["id"], "direction": "downstream"},
        )
        assert response.status_code == 200, response.text
        return [n["label"] for n in response.json()["nodes"] if n["type"] == "kpi"]

    assert kpi_nodes() == ["Accepted"]
    roles.client("editor").delete(f"{kpis_path(roles)}/{kpi['id']}")
    assert kpi_nodes() == []
    decide(roles, kpi, "accept")
    assert kpi_nodes() == ["Accepted"]


def test_a_rule_suggestion_named_like_a_suggested_or_rejected_kpi_is_still_offered(
    roles, app, warehouse
):
    suggested(roles, app, "Sum of amount (fact_loan)")

    response = roles.client("viewer").get(f"{kpis_path(roles)}/suggestions")

    assert "Sum of amount (fact_loan)" in {s["name"] for s in response.json()["items"]}


def test_exports_and_the_assistant_read_only_the_kpis_tab(roles, app):
    created(roles, name="Revenue")
    [waiting] = suggested(roles, app, "Waiting suggestion")

    pack = roles.client("viewer").get(f"/api/v1/workspaces/{roles.workspace_id}/documentation-pack")

    assert pack.status_code == 200, pack.text
    with zipfile.ZipFile(io.BytesIO(pack.content)) as archive:
        kpis_page = archive.read("kpis.md").decode()
    assert "Revenue" in kpis_page and "Waiting suggestion" not in kpis_page
    kpis = service(app)
    owner = roles.user("owner")
    assert [k.name for k in kpis.list(owner, roles.workspace_id).items] == ["Revenue"]
    with pytest.raises(Exception, match="not found"):
        kpis.get(owner, roles.workspace_id, uuid.UUID(waiting["id"]), catalog_only=True)
