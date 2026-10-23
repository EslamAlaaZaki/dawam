"""The admin-editable model price table and the cost it gives AI usage (roadmap 27.4).

Prices go through the HTTP API; calls are made through ``RoleService.gateway_for_role``
with the scripted fake provider.
"""

from __future__ import annotations

from decimal import Decimal

from dawam.modules.llm import Reply, Usage
from tests.llm.test_roles_budgets import ASK, BASE, role_service, setup  # noqa: F401


def put_price(client, model_name="big-model", **fields):
    body = {
        "model_name": model_name,
        "currency": "USD",
        "input_per_million": "3",
        "output_per_million": "15",
        "cached_input_per_million": "0.3",
        **fields,
    }
    return client.put(f"{BASE}/prices", json=body)


def call(role_service, setup, fake_llm, usage):  # noqa: F811
    fake_llm.script(Reply(text="x", usage=usage))
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))


def amounts(cost) -> dict[str, Decimal]:
    return {a["currency"]: Decimal(a["amount"]) for a in cost["amounts"]}


def test_cost_uses_the_cached_price_for_cached_tokens(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    assert put_price(admin_client).status_code == 200
    call(role_service, setup, fake_llm, Usage(1_000_000, 100_000, cache_read_tokens=400_000))

    usage = admin_client.get(f"{BASE}/usage").json()

    # 600k input at 3 + 400k cached at 0.3 + 100k output at 15 = 1.8 + 0.12 + 1.5
    assert amounts(usage["totals"]["cost"]) == {"USD": Decimal("3.42")}
    assert usage["totals"]["cost"]["unpriced_calls"] == 0
    assert amounts(usage["workspaces"][0]["totals"]["cost"]) == {"USD": Decimal("3.42")}
    assert amounts(usage["users"][0]["totals"]["cost"]) == {"USD": Decimal("3.42")}


def test_a_model_without_a_price_is_unknown_not_free(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    call(role_service, setup, fake_llm, Usage(1000, 100))

    cost = admin_client.get(f"{BASE}/usage").json()["totals"]["cost"]

    assert cost["amounts"] == []
    assert cost["unpriced_calls"] == 1


def test_priced_and_unpriced_calls_are_reported_apart(
    admin_client,
    role_service,  # noqa: F811
    setup,  # noqa: F811
    fake_llm,
):
    put_price(admin_client)
    call(role_service, setup, fake_llm, Usage(1_000_000, 0))
    role_service.record_usage(
        Usage(500, 5), role="agent", model_name="mystery", workspace_id=setup, user_id=None
    )

    cost = admin_client.get(f"{BASE}/usage").json()["totals"]["cost"]

    assert amounts(cost) == {"USD": Decimal("3")}
    assert cost["unpriced_calls"] == 1


def test_the_price_table_is_editable_and_a_price_can_be_removed(admin_client):
    assert admin_client.get(f"{BASE}/prices").json()["prices"] == []
    put_price(admin_client, "m1")
    put_price(admin_client, "m1", input_per_million="4", currency="EUR")

    [row] = admin_client.get(f"{BASE}/prices").json()["prices"]
    assert (row["model_name"], row["currency"], Decimal(row["input_per_million"])) == (
        "m1",
        "EUR",
        Decimal("4"),
    )
    assert admin_client.delete(f"{BASE}/prices", params={"model_name": "m1"}).status_code == 204
    assert admin_client.get(f"{BASE}/prices").json()["prices"] == []


def test_a_negative_price_is_refused(admin_client):
    assert put_price(admin_client, output_per_million="-1").status_code == 422
