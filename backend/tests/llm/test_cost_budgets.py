"""Monthly AI budgets in currency, enforced alongside the token budgets (roadmap 27.5).

Budgets go through the HTTP API; calls are made through ``RoleService.gateway_for_role``
with the scripted fake provider. Cost comes from the model price table.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from dawam.modules.llm import Reply, Usage
from tests.llm.test_model_prices import put_price
from tests.llm.test_roles_budgets import ASK, BASE, role_service, setup  # noqa: F401
from tests.roles import RoleClients


def spend(role_service, setup, fake_llm, tokens=1_000_000):  # noqa: F811
    """One call that costs tokens / 1M * 3 USD at the test price (input 3 per million)."""
    fake_llm.script(Reply(text="x", usage=Usage(tokens, 0)))
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))


def refused(role_service, setup):  # noqa: F811
    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))
    return caught.value


def set_installation(client, amount="5", currency="USD"):
    return client.put(
        f"{BASE}/budgets/installation/cost", json={"amount": amount, "currency": currency}
    )


def test_an_installation_currency_budget_blocks_use_when_reached_with_a_clear_message(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    put_price(admin_client)
    assert set_installation(admin_client, "5").status_code == 200
    spend(role_service, setup, fake_llm)  # 3 USD: under the budget
    spend(role_service, setup, fake_llm)  # 6 USD: over it

    error = refused(role_service, setup)

    assert error.status_code == 429 and error.code == "cost_budget_exhausted"
    assert error.details["scope"] == "installation" and error.details["currency"] == "USD"
    assert "USD" in error.message


def test_a_workspace_currency_budget_stops_that_workspace(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
    roles: RoleClients,
):
    put_price(admin_client)
    workspace = roles.workspace["id"]
    response = admin_client.put(
        f"{BASE}/budgets/workspaces/{workspace}/cost", json={"amount": "3", "currency": "USD"}
    )
    assert response.status_code == 200
    assert response.json()["workspace_cost_budgets"][0]["workspace_name"] == roles.workspace["name"]
    spend(role_service, setup, fake_llm)

    assert refused(role_service, setup).details["scope"] == "workspace"


def test_token_budgets_keep_working_beside_a_currency_budget(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    put_price(admin_client)
    set_installation(admin_client, "1000")
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 15})
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    assert refused(role_service, setup).code == "token_budget_exhausted"


def test_calls_in_another_currency_do_not_count(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    put_price(admin_client, currency="EUR")
    set_installation(admin_client, "1", "USD")

    spend(role_service, setup, fake_llm)
    spend(role_service, setup, fake_llm)  # still allowed: its cost is in EUR


def test_the_currency_budget_starts_over_next_month(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
    clock,
):
    put_price(admin_client)
    set_installation(admin_client, "1")
    spend(role_service, setup, fake_llm)
    assert refused(role_service, setup).code == "cost_budget_exhausted"

    clock.advance(timedelta(days=30))

    spend(role_service, setup, fake_llm)


def test_removing_the_currency_budget_lifts_the_limit(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    put_price(admin_client)
    set_installation(admin_client, "1")
    spend(role_service, setup, fake_llm)
    assert refused(role_service, setup).code == "cost_budget_exhausted"

    assert admin_client.delete(f"{BASE}/budgets/installation/cost").status_code == 204

    spend(role_service, setup, fake_llm)
    budgets = admin_client.get(f"{BASE}/budgets").json()
    assert budgets["installation_monthly_cost_budget"] is None


def test_budgets_report_the_currency_budget(admin_client):
    set_installation(admin_client, "12.5", "EUR")

    budget = admin_client.get(f"{BASE}/budgets").json()["installation_monthly_cost_budget"]

    assert budget["currency"] == "EUR" and Decimal(budget["amount"]) == Decimal("12.5")


def test_a_currency_budget_must_be_valid_and_for_a_known_workspace(admin_client):
    assert set_installation(admin_client, "-1").status_code == 422
    assert set_installation(admin_client, "1", "usd").status_code == 422
    unknown = "00000000-0000-0000-0000-000000000000"
    response = admin_client.put(
        f"{BASE}/budgets/workspaces/{unknown}/cost", json={"amount": "1", "currency": "USD"}
    )
    assert response.status_code == 404
