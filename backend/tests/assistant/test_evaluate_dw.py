"""AI evaluation of a Layer (spec §6.11, story 122): the ``evaluate_dw`` tool stores advisory
findings, any member can read them, editors can turn them into Change Sets, and they never
touch the score. The model is the scripted fake provider."""

from __future__ import annotations

import pytest

from dawam.modules.warehouse import EvaluationService, ModelService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.errors import ApiError
from tests.assistant import test_chat
from tests.assistant.test_source_tools import payload, use
from tests.kpis import test_kpi_links
from tests.kpis.test_kpis import created
from tests.roles import RoleClients
from tests.workspaces.test_archive_delete import archive

model = test_chat.model  # the assistant's model (a fixture)
warehouse = test_kpi_links.model  # a Data Warehouse with Core and Mart tables (a fixture)


def dw(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/data-warehouse"


def stored(roles: RoleClients, role="viewer", layer: str | None = None) -> list[dict]:
    response = roles.client(role).get(
        f"{dw(roles)}/evaluations", params={"layer": layer} if layer else None
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def finding_with_change(roles: RoleClients, warehouse: dict) -> dict:
    kpi = created(roles, name="Total loans")
    return {
        "title": "Total loans has no formula",
        "detail": "The KPI is not linked to any column.",
        "category": "uncomputable_kpi",
        "changes": [
            {
                "object_type": "kpi",
                "operation": "update",
                "object_id": kpi["id"],
                "payload": {
                    "formula_sql": "SELECT SUM(amount) FROM fact_loan",
                    "links": [warehouse["amount"]["id"]],
                },
                "label": "Total loans",
            }
        ],
    }


def test_reading_a_layer_gives_the_model_its_tables_grain_and_kpis(
    roles: RoleClients, model, warehouse, fake_llm
):
    created(roles, name="Total loans")

    status, seen = use(roles, fake_llm, "evaluate_dw", layer="core")

    assert status == "ok"
    review = payload(seen)
    [fact] = review["tables"]
    assert fact["name"] == "fact_loan" and {c["name"] for c in fact["columns"]} >= {"amount"}
    assert review["other_tables"] == ["mart.mart_loan (fact)"]
    assert [k["name"] for k in review["kpis"]] == ["Total loans"]
    assert stored(roles) == []  # reading stores nothing


def test_a_viewer_stores_findings_and_every_member_reads_them(
    roles: RoleClients, model, warehouse, fake_llm
):
    status, seen = use(
        roles,
        fake_llm,
        "evaluate_dw",
        layer="core",
        findings=[
            {
                "title": "Grain is ambiguous",
                "detail": "fact_loan's grain does not say whether it is per loan or per payment.",
                "category": "ambiguous_grain",
                "severity": "high",
                "table_id": warehouse["core"]["id"],
            }
        ],
    )

    assert status == "ok"
    assert payload(seen)["finding_count"] == 1
    for role in ("viewer", "editor", "owner"):
        [evaluation] = stored(roles, role, layer="core")
        [finding] = evaluation["findings"]
        assert finding["category"] == "ambiguous_grain" and finding["items"] == []
    assert stored(roles, layer="mart") == []


def test_findings_never_change_the_score(roles: RoleClients, model, warehouse, fake_llm):
    before = roles.client("viewer").get(f"{dw(roles)}/score").json()

    use(
        roles,
        fake_llm,
        "evaluate_dw",
        layer="core",
        findings=[{"title": "Bad", "detail": "Everything is wrong.", "severity": "high"}],
    )

    after = roles.client("viewer").get(f"{dw(roles)}/score").json()
    assert (after["score"], after["grade"], after["failed_checks"]) == (
        before["score"],
        before["grade"],
        before["failed_checks"],
    )


def test_an_archived_workspace_refuses_to_evaluate(roles: RoleClients, model, warehouse):
    state = roles.app.state
    clock = state.services.clock
    workspaces = WorkspaceService(state.engine, clock=clock)
    service = EvaluationService(
        state.engine,
        workspaces=workspaces,
        model=ModelService(state.engine, workspaces=workspaces, clock=clock),
        clock=clock,
    )
    viewer = roles.user("viewer")
    roles.client("viewer")  # makes the user a member
    assert archive(roles).status_code == 204

    for call in (
        lambda: service.review(viewer, roles.workspace_id, "core"),
        lambda: service.store(viewer, roles.workspace_id, "core", []),
    ):
        with pytest.raises(ApiError) as refused:
            call()
        assert refused.value.code == "workspace_archived"
    assert stored(roles) == []


def test_before_set_up_the_tool_reports_an_error(roles: RoleClients, model, fake_llm):
    status, _ = use(roles, fake_llm, "evaluate_dw", layer="core")

    assert status == "error"


def test_an_editor_turns_a_finding_into_a_change_set_and_a_viewer_cannot(
    roles: RoleClients, model, warehouse, fake_llm
):
    use(
        roles,
        fake_llm,
        "evaluate_dw",
        layer="core",
        findings=[finding_with_change(roles, warehouse)],
    )
    [evaluation] = stored(roles)
    url = f"{dw(roles)}/evaluations/{evaluation['id']}/findings/0/change-set"

    assert roles.client("viewer").post(url).status_code == 403
    response = roles.client("editor").post(url)

    assert response.status_code == 201, response.text
    created_set = response.json()
    assert created_set["item_count"] == 1
    detail = roles.client("editor").get(
        f"/api/v1/workspaces/{roles.workspace_id}/change-sets/{created_set['change_set_id']}"
    )
    assert detail.json()["change_set"]["origin"] == "ai"
    assert detail.json()["change_set"]["status"] == "pending"


def test_an_advice_only_finding_has_no_change_set(roles: RoleClients, model, warehouse, fake_llm):
    use(
        roles,
        fake_llm,
        "evaluate_dw",
        layer="core",
        findings=[{"title": "Rename it", "detail": "A clearer name would help."}],
    )
    [evaluation] = stored(roles)
    base = f"{dw(roles)}/evaluations/{evaluation['id']}/findings"

    advice = roles.client("editor").post(f"{base}/0/change-set")
    missing = roles.client("editor").post(f"{base}/5/change-set")

    assert advice.status_code == 422 and advice.json()["error"]["code"] == "no_changes"
    assert missing.status_code == 404
