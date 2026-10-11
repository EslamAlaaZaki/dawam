"""Rule-based KPI suggestions over the API (spec §6.13, story 77).

The rules themselves are tested on fixture schemas in ``test_kpi_suggestions_rules``; these
tests cover the computed list, accepting, dismissing and feasibility end to end.
"""

from __future__ import annotations

import pytest

from dawam.modules.audit import AuditService
from tests.kpis.test_kpi_links import column, dw, put_links, table
from tests.kpis.test_kpis import created, error_code, kpis_path
from tests.roles import RoleClients

INTEGER = {"type": "integer"}


def suggestions_path(roles: RoleClients) -> str:
    return f"{kpis_path(roles)}/suggestions"


def listed(roles: RoleClients, as_role: str = "viewer") -> list[dict]:
    response = roles.client(as_role).get(suggestions_path(roles))
    assert response.status_code == 200, response.text
    return response.json()["items"]


def by_name(roles: RoleClients) -> dict[str, dict]:
    return {s["name"]: s for s in listed(roles)}


def map_mart_amount(roles: RoleClients, model: dict) -> None:
    response = roles.client("editor").put(
        f"{dw(roles)}/tables/{model['mart']['id']}/mapping/columns/{model['amount_m']['id']}",
        json={
            "mapping_type": "direct",
            "rule_text": "As is",
            "sql_expression": "fact_loan.amount",
        },
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def model(roles: RoleClients) -> dict:
    """Core ``dim_customer`` and ``fact_loan`` (customer_key, amount, rate, fee), Mart
    ``mart_loan`` (amount_m); nothing is mapped yet."""
    response = roles.client("editor").post(dw(roles), json={"target_platform": "postgresql"})
    assert response.status_code == 201, response.text
    customer = table(roles, "dim_customer", "core")
    column(roles, customer["id"], "customer_key", "sk", data_type=INTEGER)
    core = table(roles, "fact_loan", "core", "fact")
    mart = table(roles, "mart_loan", "mart", "fact")
    return {
        "core": core,
        "mart": mart,
        "customer_key": column(
            roles,
            core["id"],
            "customer_key",
            "fk",
            data_type=INTEGER,
            references_table_id=customer["id"],
        ),
        "amount": column(roles, core["id"], "amount", "measure", additivity="additive"),
        "fee": column(roles, core["id"], "fee", "measure", additivity="additive"),
        "rate": column(roles, core["id"], "rate", "measure", additivity="non_additive"),
        "amount_m": column(roles, mart["id"], "amount_m", "measure", additivity="additive"),
    }


def test_the_list_is_empty_before_the_data_warehouse_is_set_up(roles: RoleClients):
    assert listed(roles) == []


def test_the_dw_schema_gets_a_computed_list_of_suggestions(roles: RoleClients, model):
    suggestions = by_name(roles)

    assert sorted(suggestions) == [
        "Amount per fee (fact_loan)",
        "Average of amount (fact_loan)",
        "Average of amount_m (mart_loan)",
        "Average of fee (fact_loan)",
        "Distinct customer count (fact_loan)",
        "Fee per amount (fact_loan)",
        "Sum of amount (fact_loan)",
        "Sum of amount_m (mart_loan)",
        "Sum of fee (fact_loan)",
    ]
    total = suggestions["Sum of amount (fact_loan)"]
    assert total["kind"] == "sum"
    assert total["layer"] == "core"
    assert total["formula_sql"] == "SELECT SUM(amount) AS sum_amount FROM core.fact_loan"
    assert total["column_ids"] == [model["amount"]["id"]]
    # a computed list: no KPI exists until one is accepted
    assert roles.client("viewer").get(kpis_path(roles)).json()["items"] == []


def test_feasibility_follows_the_mappings(roles: RoleClients, model):
    assert by_name(roles)["Sum of amount_m (mart_loan)"]["feasibility"] == "needs_mapping"

    map_mart_amount(roles, model)

    assert by_name(roles)["Sum of amount_m (mart_loan)"]["feasibility"] == "computable"


def test_a_core_measure_read_by_a_mart_column_is_suggested_once_at_the_mart(
    roles: RoleClients, model
):
    map_mart_amount(roles, model)

    suggestions = by_name(roles)

    assert "Sum of amount (fact_loan)" not in suggestions
    assert suggestions["Sum of amount_m (mart_loan)"]["layer"] == "mart"
    assert suggestions["Sum of amount_m (mart_loan)"]["column_ids"] == [model["amount_m"]["id"]]
    assert "Sum of fee (fact_loan)" in suggestions  # not read by the Mart


def test_accepting_a_suggestion_creates_a_draft_kpi_with_origin_rule(
    roles: RoleClients, model, app
):
    suggestion = by_name(roles)["Sum of amount (fact_loan)"]

    response = roles.client("editor").post(
        f"{suggestions_path(roles)}/accept", json={"key": suggestion["key"]}
    )

    assert response.status_code == 201, response.text
    kpi = response.json()
    assert kpi["origin"] == "rule"
    assert kpi["status"] == "draft"
    assert kpi["source_system_id"] is None
    assert kpi["name"] == suggestion["name"]
    assert kpi["formula_sql"] == suggestion["formula_sql"]
    assert kpi["formula_text"] == suggestion["formula_text"]
    assert kpi["rationale"] is None
    opened = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}")
    assert opened.json() == kpi
    links = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}/links").json()
    assert [(i["table_name"], i["column_name"]) for i in links["items"]] == [
        ("fact_loan", "amount")
    ]
    [entry] = AuditService(app.state.engine).list(
        roles.workspace_id, entity_type="kpi", entity_id=kpi["id"]
    )
    assert entry.new["origin"] == "rule"
    assert entry.new["links"] == [model["amount"]["id"]]
    # accepted: it is a KPI now, not a suggestion; the others stay
    assert suggestion["name"] not in by_name(roles)
    assert "Average of amount (fact_loan)" in by_name(roles)


def test_an_accepted_suggestion_cannot_be_accepted_again(roles: RoleClients, model):
    key = by_name(roles)["Sum of fee (fact_loan)"]["key"]
    body = {"key": key}
    assert (
        roles.client("editor").post(f"{suggestions_path(roles)}/accept", json=body).status_code
        == 201
    )

    again = roles.client("editor").post(f"{suggestions_path(roles)}/accept", json=body)

    assert again.status_code == 404
    assert error_code(again) == "not_found"


def test_the_formula_of_an_accepted_suggestion_passes_the_kpi_checks(roles: RoleClients, model):
    for suggestion in listed(roles):
        response = roles.client("editor").post(
            f"{suggestions_path(roles)}/accept", json={"key": suggestion["key"]}
        )
        assert response.status_code == 201, (suggestion["name"], response.text)

    assert listed(roles) == []


def test_a_dismissed_suggestion_stays_hidden(roles: RoleClients, model):
    key = by_name(roles)["Average of fee (fact_loan)"]["key"]

    first = roles.client("editor").post(f"{suggestions_path(roles)}/dismiss", json={"key": key})
    second = roles.client("editor").post(f"{suggestions_path(roles)}/dismiss", json={"key": key})

    assert first.status_code == second.status_code == 204
    assert "Average of fee (fact_loan)" not in by_name(roles)
    assert "Average of amount (fact_loan)" in by_name(roles)
    # it does not come back when the model changes
    map_mart_amount(roles, model)
    assert "Average of fee (fact_loan)" not in by_name(roles)
    accept = roles.client("editor").post(f"{suggestions_path(roles)}/accept", json={"key": key})
    assert accept.status_code == 404


def test_a_dismissal_survives_a_measure_moving_to_the_mart(roles: RoleClients, model):
    key = by_name(roles)["Sum of amount (fact_loan)"]["key"]
    roles.client("editor").post(f"{suggestions_path(roles)}/dismiss", json={"key": key})

    map_mart_amount(roles, model)

    assert "Sum of amount_m (mart_loan)" not in by_name(roles)


def test_an_unknown_suggestion_is_404(roles: RoleClients, model):
    for action in ("accept", "dismiss"):
        response = roles.client("editor").post(
            f"{suggestions_path(roles)}/{action}", json={"key": "sum:nothing"}
        )
        assert response.status_code == 404
        assert error_code(response) == "not_found"


def test_only_editors_accept_or_dismiss_but_any_member_reads(roles: RoleClients, model):
    key = by_name(roles)["Sum of amount (fact_loan)"]["key"]

    for action in ("accept", "dismiss"):
        denied = roles.client("viewer").post(
            f"{suggestions_path(roles)}/{action}", json={"key": key}
        )
        assert denied.status_code == 403
    assert roles.client("viewer").get(suggestions_path(roles)).status_code == 200


def test_a_name_already_taken_by_a_kpi_hides_the_suggestion(roles: RoleClients, model):
    created(roles, name="Sum of amount (fact_loan)")

    assert "Sum of amount (fact_loan)" not in by_name(roles)


def feasibility(roles: RoleClients, kpi: dict) -> dict:
    response = roles.client("viewer").get(f"{kpis_path(roles)}/{kpi['id']}/feasibility")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_kpi_with_nothing_in_the_dw_schema_needs_data(roles: RoleClients, model):
    # an AI suggestion is such a KPI: a draft without links or formula SQL
    kpi = created(roles, name="Customer happiness")

    assert feasibility(roles, kpi) == {"feasibility": "needs_data", "unmapped": []}


def test_a_kpi_over_unmapped_columns_needs_mapping_until_they_are_mapped(roles: RoleClients, model):
    kpi = created(roles, name="Loan volume")
    put_links(roles, kpi, [model["amount_m"]["id"]])

    assert feasibility(roles, kpi) == {
        "feasibility": "needs_mapping",
        "unmapped": ["mart_loan.amount_m"],
    }

    map_mart_amount(roles, model)

    assert feasibility(roles, kpi) == {"feasibility": "computable", "unmapped": []}


def test_the_formula_sql_counts_as_inputs_of_the_feasibility(roles: RoleClients, model):
    kpi = created(roles, name="Fees", formula_sql="SELECT SUM(fee) FROM fact_loan")

    assert feasibility(roles, kpi) == {
        "feasibility": "needs_mapping",
        "unmapped": ["fact_loan.fee"],
    }
