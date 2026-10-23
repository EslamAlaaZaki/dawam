"""Monthly token budgets and AI usage, and how a call's model is resolved from the
Workspace (spec §6.18, stories 157, 159, 162).

Admin behaviour goes through the HTTP API; a feature's model call goes through
``RoleService.gateway_for_role``, the seam every AI feature uses. The scripted fake
provider stands behind every registered provider (``fake_llm``).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from dawam.modules.llm import LlmError, Message, ProviderService, Reply, RoleService, Usage
from dawam.modules.llm.gateway import RATE_LIMIT
from tests.roles import RoleClients

BASE = "/api/v1/admin/llm"
ASK = [Message("user", "Hi")]


def add_model(client, provider, name, roles=("agent",)) -> dict:
    """Register a model (no role), then set its roles in the roles table."""
    model = client.post(f"{BASE}/providers/{provider['id']}/models", json={"name": name}).json()
    response = client.put(f"{BASE}/models/{model['id']}/roles", json={"roles": list(roles)})
    assert response.status_code == 200, response.text
    return response.json()


def register(client, name, roles=("agent",), tested=True, provider=None) -> dict:
    if provider is None:
        provider = client.post(
            f"{BASE}/providers",
            json={"name": f"P-{name}", "base_url": "http://llm.test/v1", "internal": True},
        ).json()
    model = add_model(client, provider, name, roles)
    if tested:
        assert client.post(f"{BASE}/models/{model['id']}/test").json()["test_ok"] is True
    return model


def use_models(client, workspace_id, agent, light=None, embedding=None, **fields):
    """The owner (``client``) gives the Workspace one default agent model, and a light and
    an embedding model when given."""
    return client.put(
        f"/api/v1/workspaces/{workspace_id}/ai-settings",
        json={
            "internal_only": False,
            "data_sharing_level": "metadata",
            "agent_model_ids": [agent["id"]],
            "light_model_ids": [light["id"]] if light else [],
            "embedding_model_id": embedding["id"] if embedding else None,
            **fields,
        },
    )


def code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
def role_service(app, clock, fake_llm):
    state = app.state
    providers = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        adapters=state.services.llm_adapters,
    )
    return RoleService(state.engine, providers=providers, clock=clock)


@pytest.fixture
def setup(admin_client, fake_llm, roles: RoleClients):
    """The Workspace's id, with a tested agent model as its default."""
    assert roles.workspace_id  # created first, while there is no model to choose
    agent = register(admin_client, "big-model")
    assert use_models(roles.client("owner"), roles.workspace_id, agent).status_code == 200
    return roles.workspace_id


# -- resolving a role from the Workspace ----------------------------------------------


def test_light_tasks_use_the_default_agent_model_when_the_workspace_has_no_light_model(
    role_service, setup, fake_llm
):
    gateway = role_service.gateway_for_role("light", workspace_id=setup)
    list(gateway.chat(ASK))

    assert gateway.role == "agent"
    assert fake_llm.calls[-1].model == "big-model"


def test_a_role_without_a_model_says_so(role_service, roles: RoleClients):
    with pytest.raises(Exception) as caught:
        role_service.gateway_for_role("agent", workspace_id=roles.workspace_id)

    assert caught.value.code == "model_role_unassigned"


def test_the_installation_has_no_role_defaults_any_more(admin_client):
    assert admin_client.get(f"{BASE}/roles").status_code in (404, 405)


# -- usage -----------------------------------------------------------------------------


def test_every_call_records_tokens_with_workspace_user_and_role(
    admin_client, role_service, setup, fake_llm, roles: RoleClients
):
    workspace, user = roles.workspace["id"], roles.user("owner").id
    fake_llm.script(Reply(text="Hello", usage=Usage(100, 20)), Reply(text="x" * 40, usage=None))

    list(role_service.gateway_for_role("agent", workspace_id=workspace, user_id=user).chat(ASK))
    list(role_service.gateway_for_role("agent", workspace_id=workspace, user_id=user).chat(ASK))

    usage = admin_client.get(f"{BASE}/usage").json()
    [per_workspace] = usage["workspaces"]
    assert per_workspace["workspace_id"] == workspace
    assert per_workspace["workspace_name"] == roles.workspace["name"]
    assert per_workspace["totals"]["calls"] == 2
    assert per_workspace["totals"]["estimated_calls"] == 1  # the second reported no usage
    assert per_workspace["totals"]["prompt_tokens"] >= 100
    assert per_workspace["tokens_by_role"]["agent"] == per_workspace["totals"]["total_tokens"]
    [per_user] = usage["users"]
    assert per_user["user_id"] == str(user) and per_user["email"] == "owner@example.com"
    assert usage["totals"]["calls"] == 2


def test_embedding_calls_are_recorded_as_estimated_usage(
    admin_client, role_service, fake_llm, roles: RoleClients
):
    assert roles.workspace_id
    agent = register(admin_client, "big")
    embedding = register(admin_client, "embed", roles=("embedding",))
    use_models(roles.client("owner"), roles.workspace_id, agent, embedding=embedding)

    role_service.gateway_for_role(
        "embedding", workspace_id=roles.workspace["id"], user_id=roles.user("owner").id
    ).embed(["some document text"])

    [per_workspace] = admin_client.get(f"{BASE}/usage").json()["workspaces"]
    assert per_workspace["tokens_by_role"] == {"embedding": per_workspace["totals"]["total_tokens"]}
    assert per_workspace["totals"]["estimated_calls"] == 1


def test_usage_is_filterable_by_month(admin_client, role_service, setup, fake_llm, clock):
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))
    # An admin's session would time out over such a gap, so the report is read directly.
    clock.advance(timedelta(days=40))
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    january, february = role_service.usage_report("2026-01"), role_service.usage_report("2026-02")

    assert (january.totals.calls, february.totals.calls) == (1, 1)
    assert role_service.usage_report().month == "2026-02"


def test_usage_defaults_to_this_month_and_refuses_a_malformed_one(admin_client):
    assert admin_client.get(f"{BASE}/usage").json()["month"] == "2026-01"
    assert admin_client.get(f"{BASE}/usage", params={"month": "2026-01"}).status_code == 200
    assert admin_client.get(f"{BASE}/usage", params={"month": "soon"}).status_code == 422


# -- budgets ---------------------------------------------------------------------------


def test_an_exhausted_installation_budget_refuses_the_next_call_with_a_clear_error(
    admin_client, role_service, setup, fake_llm
):
    assert (
        admin_client.put(
            f"{BASE}/budgets/installation", json={"monthly_token_budget": 15}
        ).status_code
        == 200
    )
    # Uses 15 tokens: now at the limit.
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    assert caught.value.status_code == 429 and caught.value.code == "token_budget_exhausted"
    assert caught.value.details["scope"] == "installation"
    assert len(fake_llm.calls) == 4 + 1  # the test-connection probe's four, then one call


def test_cached_input_is_recorded_separately_and_still_counts_against_the_budget(
    admin_client, role_service, setup, fake_llm, app
):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from dawam.modules.llm.tables import UsageRecord

    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 100})
    fake_llm.script(
        Reply(text="x", usage=Usage(95, 5, cache_read_tokens=80, cache_write_tokens=10))
    )
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    with Session(app.state.engine) as db:
        row = db.scalars(select(UsageRecord)).one()
    assert (row.prompt_tokens, row.cache_read_tokens, row.cache_write_tokens) == (95, 80, 10)
    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))
    assert caught.value.code == "token_budget_exhausted"


def test_a_workspace_budget_stops_that_workspace_only(
    admin_client, role_service, setup, fake_llm, roles: RoleClients
):
    workspace = roles.workspace["id"]
    response = admin_client.put(
        f"{BASE}/budgets/workspaces/{workspace}", json={"monthly_token_budget": 10}
    )
    assert response.status_code == 200
    assert response.json()["workspaces"][0]["workspace_name"] == roles.workspace["name"]
    list(role_service.gateway_for_role("agent", workspace_id=workspace).chat(ASK))

    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("agent", workspace_id=workspace).chat(ASK))
    other = roles.client("owner").post(
        "/api/v1/workspaces", json={"name": "Other", "agent_model_id": big_model(admin_client)}
    )
    list(role_service.gateway_for_role("agent", workspace_id=other.json()["id"]).chat(ASK))

    assert caught.value.details["scope"] == "workspace"


def test_the_budget_starts_over_next_month(admin_client, role_service, setup, fake_llm, clock):
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 15})
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    clock.advance(timedelta(days=30))

    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))


def test_removing_a_budget_lifts_the_limit(
    admin_client, role_service, setup, fake_llm, roles: RoleClients
):
    workspace = roles.workspace["id"]
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 1})
    admin_client.put(f"{BASE}/budgets/workspaces/{workspace}", json={"monthly_token_budget": 1})
    list(role_service.gateway_for_role("agent", workspace_id=workspace).chat(ASK))

    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": None})
    assert admin_client.delete(f"{BASE}/budgets/workspaces/{workspace}").status_code == 204

    list(role_service.gateway_for_role("agent", workspace_id=workspace).chat(ASK))
    budgets = admin_client.get(f"{BASE}/budgets").json()
    assert budgets["installation_monthly_token_budget"] is None and budgets["workspaces"] == []


def test_a_budget_for_an_unknown_workspace_is_not_found(admin_client):
    response = admin_client.put(
        f"{BASE}/budgets/workspaces/00000000-0000-0000-0000-000000000000",
        json={"monthly_token_budget": 5},
    )

    assert response.status_code == 404 and code(response) == "workspace_not_found"


def test_non_ai_features_keep_working_when_the_budget_is_used_up(
    admin_client, role_service, setup, fake_llm
):
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 0})

    assert admin_client.get(f"{BASE}/providers").status_code == 200
    assert admin_client.get("/api/v1/workspaces").status_code == 200


def test_a_failed_call_is_recorded_with_estimated_tokens(
    admin_client, role_service, setup, fake_llm
):
    fake_llm.script(*[LlmError(RATE_LIMIT, "slow down")] * 4)

    with pytest.raises(LlmError):
        list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))

    totals = admin_client.get(f"{BASE}/usage").json()["totals"]
    assert totals["calls"] == 1 and totals["estimated_calls"] == 1


def big_model(client) -> str:
    [provider] = client.get(f"{BASE}/providers").json()["items"]
    return provider["models"][0]["id"]


def test_an_abandoned_stream_is_recorded(admin_client, role_service, setup, fake_llm):
    fake_llm.script(Reply(text="one two three four", usage=Usage(50, 10)))

    stream = role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK, stream=True)
    next(stream)
    stream.close()

    totals = admin_client.get(f"{BASE}/usage").json()["totals"]
    assert totals["calls"] == 1 and totals["estimated_calls"] == 1
