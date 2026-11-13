"""Rule-based suggestions in the review (spec §6.13, #598): the Data Warehouse page gets them
with the KPI list in one request. Accept creates an accepted `rule` KPI, reject dismisses,
accept after all clears the dismissal, deleting an accepted one moves it to Rejected and keeps
the suggestion dismissed."""

from __future__ import annotations

from tests.kpis import test_kpi_suggestions as suggestions
from tests.kpis.test_kpi_suggestions import suggestions_path
from tests.kpis.test_kpis import error_code, kpis_path
from tests.roles import RoleClients

loan_model = suggestions.model  # the fixture, under a name tests can take as a parameter

NAME = "Sum of amount (fact_loan)"


def page(roles: RoleClients, as_role: str = "editor", **query) -> dict:
    response = roles.client(as_role).get(
        kpis_path(roles), params={"review": "all", "rule_suggestions": "true", **query}
    )
    assert response.status_code == 200, response.text
    return response.json()


def open_names(roles: RoleClients) -> dict[str, dict]:
    return {s["name"]: s for s in page(roles)["rule_suggestions"]}


def rejected_names(roles: RoleClients) -> dict[str, dict]:
    return {s["name"]: s for s in page(roles)["rejected_rule_suggestions"]}


def post(roles: RoleClients, action: str, key: str):
    return roles.client("editor").post(f"{suggestions_path(roles)}/{action}", json={"key": key})


def test_the_list_carries_the_rule_suggestions_in_one_request(roles: RoleClients, loan_model):
    body = page(roles)

    suggestion = {s["name"]: s for s in body["rule_suggestions"]}[NAME]
    assert suggestion["feasibility"] == "needs_mapping"
    assert suggestion["formula_sql"]
    assert body["rejected_rule_suggestions"] == []
    assert body["items"] == []
    # not asked for, or not the review: no suggestions
    plain = roles.client("editor").get(kpis_path(roles), params={"review": "all"}).json()
    assert plain["rule_suggestions"] == [] and plain["rejected_rule_suggestions"] == []
    denied = roles.client("editor").get(kpis_path(roles), params={"rule_suggestions": "true"})
    assert denied.status_code == 422
    assert error_code(denied) == "invalid_filter"
    # viewers do not see the review
    assert (
        roles.client("viewer")
        .get(kpis_path(roles), params={"review": "all", "rule_suggestions": "true"})
        .status_code
        == 403
    )


def test_accepting_creates_an_accepted_rule_kpi_in_the_kpis_tab(roles: RoleClients, loan_model):
    key = open_names(roles)[NAME]["key"]

    response = post(roles, "accept", key)

    assert response.status_code == 201, response.text
    kpi = response.json()
    assert kpi["origin"] == "rule" and kpi["source_system_id"] is None
    assert kpi["review_state"] == "accepted" and kpi["decision"] == "accepted"
    assert kpi["decided_by"] == str(roles.user("editor").id)
    assert kpi["decided_at"] is not None
    catalog = roles.client("viewer").get(kpis_path(roles)).json()["items"]
    assert [k["name"] for k in catalog] == [NAME]
    assert NAME not in open_names(roles)


def test_rejecting_lists_it_as_rejected_and_rule_runs_never_propose_it_again(
    roles: RoleClients, loan_model
):
    key = open_names(roles)[NAME]["key"]

    assert post(roles, "dismiss", key).status_code == 204

    assert NAME not in open_names(roles)
    rejected = rejected_names(roles)[NAME]
    assert rejected["key"] == key
    assert rejected["decided_by"] == str(roles.user("editor").id)
    assert rejected["decided_at"] is not None
    assert roles.client("viewer").get(suggestions_path(roles)).json()["items"] != []
    assert NAME not in {
        s["name"] for s in roles.client("viewer").get(suggestions_path(roles)).json()["items"]
    }


def test_accepting_after_all_clears_the_dismissal_and_creates_the_kpi(
    roles: RoleClients, loan_model
):
    key = open_names(roles)[NAME]["key"]
    post(roles, "dismiss", key)

    response = post(roles, "accept", key)

    assert response.status_code == 201, response.text
    assert response.json()["review_state"] == "accepted"
    body = page(roles)
    assert NAME not in {s["name"] for s in body["rejected_rule_suggestions"]}
    assert NAME not in {s["name"] for s in body["rule_suggestions"]}
    assert [k["name"] for k in body["items"]] == [NAME]


def test_deleting_an_accepted_rule_kpi_moves_it_to_rejected_and_keeps_it_dismissed(
    roles: RoleClients, loan_model
):
    key = open_names(roles)[NAME]["key"]
    kpi = post(roles, "accept", key).json()

    deleted = roles.client("editor").delete(f"{kpis_path(roles)}/{kpi['id']}")

    assert deleted.status_code == 204
    body = page(roles)
    [removed] = [k for k in body["items"] if k["name"] == NAME]
    assert removed["review_state"] == "rejected" and removed["decision"] == "removed"
    assert NAME not in {s["name"] for s in body["rule_suggestions"]}
    # the rejected KPI is the one row, not also a rejected suggestion
    assert NAME not in {s["name"] for s in body["rejected_rule_suggestions"]}
    assert roles.client("viewer").get(kpis_path(roles)).json()["items"] == []
    # even renamed, it never comes back; accepting it after all brings the KPI back
    again = roles.client("editor").post(f"{kpis_path(roles)}/{kpi['id']}/accept")
    assert again.status_code == 200
    assert again.json()["review_state"] == "accepted"
    assert NAME not in {s["name"] for s in page(roles)["rule_suggestions"]}
    # a removed KPI, deleted then dismissed again: no duplicate dismissal errors
    roles.client("editor").delete(f"{kpis_path(roles)}/{kpi['id']}")
    assert post(roles, "dismiss", key).status_code == 204


def test_a_renamed_accepted_rule_kpi_does_not_bring_its_suggestion_back(
    roles: RoleClients, loan_model
):
    key = open_names(roles)[NAME]["key"]
    kpi = post(roles, "accept", key).json()

    renamed = roles.client("editor").patch(
        f"{kpis_path(roles)}/{kpi['id']}", json={"version": kpi["version"], "name": "Loan total"}
    )

    assert renamed.status_code == 200, renamed.text
    assert NAME not in open_names(roles)
