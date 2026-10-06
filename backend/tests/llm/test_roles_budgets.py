"""Model roles, monthly token budgets and AI usage (spec §6.18, stories 157, 159, 162).

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


def register(client, name, roles=("agent",), tested=True, provider=None) -> dict:
    if provider is None:
        provider = client.post(
            f"{BASE}/providers",
            json={"name": f"P-{name}", "base_url": "http://llm.test/v1", "internal": True},
        ).json()
    model = client.post(
        f"{BASE}/providers/{provider['id']}/models", json={"name": name, "roles": list(roles)}
    ).json()
    if tested:
        assert client.post(f"{BASE}/models/{model['id']}/test").json()["test_ok"] is True
    return model


def assign(client, agent, light=None, embedding=None):
    return client.put(
        f"{BASE}/roles",
        json={
            "agent_model_id": agent["id"],
            "light_model_id": light["id"] if light else None,
            "embedding_model_id": embedding["id"] if embedding else None,
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
def setup(admin_client, fake_llm):
    agent = register(admin_client, "big-model")
    assert assign(admin_client, agent).status_code == 200
    return agent


# -- roles -----------------------------------------------------------------------------


def test_roles_are_empty_until_an_admin_assigns_them(admin_client):
    body = admin_client.get(f"{BASE}/roles").json()

    assert body["agent_model_id"] is None and body["light_model_id"] is None
    assert body["embedding_model_id"] is None and body["reindex_needed"] is False


def test_an_admin_assigns_models_to_the_three_roles(admin_client, fake_llm):
    agent = register(admin_client, "big")
    light = register(admin_client, "small")
    embedding = register(admin_client, "embed", roles=("embedding",))

    response = assign(admin_client, agent, light, embedding)

    assert response.status_code == 200, response.text
    assert response.json()["agent_model_id"] == agent["id"]
    assert response.json()["light_model_id"] == light["id"]
    assert admin_client.get(f"{BASE}/roles").json()["embedding_model_id"] == embedding["id"]


def test_the_agent_role_is_required(admin_client, fake_llm):
    light = register(admin_client, "small")

    response = admin_client.put(f"{BASE}/roles", json={"light_model_id": light["id"]})

    assert response.status_code == 422


def test_only_a_tested_model_can_be_assigned(admin_client, fake_llm):
    untested = register(admin_client, "fresh", tested=False)

    response = assign(admin_client, untested)

    assert response.status_code == 422 and code(response) == "model_not_tested"


def test_an_unregistered_model_or_the_wrong_kind_of_model_is_refused(admin_client, fake_llm):
    agent = register(admin_client, "big")
    embedding = register(admin_client, "embed", roles=("embedding",))

    unknown = assign(admin_client, {"id": "00000000-0000-0000-0000-000000000000"})
    chat_as_embedding = assign(admin_client, agent, embedding=agent)
    embedding_as_agent = assign(admin_client, embedding)

    assert [r.status_code for r in (unknown, chat_as_embedding, embedding_as_agent)] == [422] * 3
    assert {code(r) for r in (unknown, chat_as_embedding, embedding_as_agent)} == {
        "invalid_model_role"
    }


def test_light_tasks_use_the_agent_model_when_no_light_model_is_assigned(
    admin_client, role_service, setup, fake_llm
):
    gateway = role_service.gateway_for_role("light")
    list(gateway.chat(ASK))

    assert gateway.role == "agent"
    assert fake_llm.calls[-1].model == "big-model"


def test_light_tasks_use_the_light_model_unless_it_is_not_allowed(
    admin_client, role_service, setup, fake_llm
):
    light = register(admin_client, "small")
    assert assign(admin_client, setup, light).status_code == 200

    list(role_service.gateway_for_role("light").chat(ASK))
    assert fake_llm.calls[-1].model == "small"

    fallback = role_service.gateway_for_role("light", light_allowed=False)
    list(fallback.chat(ASK))
    assert fake_llm.calls[-1].model == "big-model" and fallback.role == "agent"


def test_a_role_without_a_model_says_so(role_service):
    with pytest.raises(Exception) as caught:
        role_service.gateway_for_role("agent")

    assert caught.value.code == "model_role_unassigned"


# -- re-indexing -----------------------------------------------------------------------


def test_switching_the_embedding_model_records_that_reindexing_is_needed(admin_client, fake_llm):
    agent = register(admin_client, "big")
    first = register(admin_client, "embed-1", roles=("embedding",))
    second = register(admin_client, "embed-2", roles=("embedding",))

    assert assign(admin_client, agent, embedding=first).json()["reindex_needed"] is True
    assert assign(admin_client, agent, embedding=second).json()["reindex_reason"] == (
        "embedding_model_changed"
    )


def test_assigning_the_same_embedding_model_again_does_not_flag_anything_new(
    admin_client, role_service, fake_llm
):
    agent = register(admin_client, "big")
    embedding = register(admin_client, "embed", roles=("embedding",))
    assign(admin_client, agent, embedding=embedding)
    role_service.mark_reindexed()

    assert assign(admin_client, agent, embedding=embedding).json()["reindex_needed"] is False


def test_a_changed_vector_dimension_records_that_reindexing_is_needed(
    admin_client, role_service, fake_llm
):
    agent = register(admin_client, "big")
    embedding = register(admin_client, "embed", roles=("embedding",))
    assign(admin_client, agent, embedding=embedding)
    role_service.mark_reindexed()
    fake_llm.dimension = 8

    admin_client.post(f"{BASE}/models/{embedding['id']}/test")

    body = admin_client.get(f"{BASE}/roles").json()
    assert body["reindex_needed"] is True
    assert body["reindex_reason"] == "embedding_dimension_changed"


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
    agent = register(admin_client, "big")
    embedding = register(admin_client, "embed", roles=("embedding",))
    assign(admin_client, agent, embedding=embedding)

    role_service.gateway_for_role(
        "embedding", workspace_id=roles.workspace["id"], user_id=roles.user("owner").id
    ).embed(["some document text"])

    [per_workspace] = admin_client.get(f"{BASE}/usage").json()["workspaces"]
    assert per_workspace["tokens_by_role"] == {"embedding": per_workspace["totals"]["total_tokens"]}
    assert per_workspace["totals"]["estimated_calls"] == 1


def test_usage_is_filterable_by_month(admin_client, role_service, setup, fake_llm, clock):
    list(role_service.gateway_for_role("agent").chat(ASK))
    # An admin's session would time out over such a gap, so the report is read directly.
    clock.advance(timedelta(days=40))
    list(role_service.gateway_for_role("agent").chat(ASK))

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
    list(role_service.gateway_for_role("agent").chat(ASK))  # uses 15 tokens: now at the limit

    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("agent").chat(ASK))

    assert caught.value.status_code == 429 and caught.value.code == "token_budget_exhausted"
    assert caught.value.details["scope"] == "installation"
    assert len(fake_llm.calls) == 4 + 1  # the test-connection probe's four, then one call


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
    list(role_service.gateway_for_role("agent").chat(ASK))  # no Workspace: unaffected

    assert caught.value.details["scope"] == "workspace"


def test_the_budget_starts_over_next_month(admin_client, role_service, setup, fake_llm, clock):
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 15})
    list(role_service.gateway_for_role("agent").chat(ASK))

    clock.advance(timedelta(days=30))

    list(role_service.gateway_for_role("agent").chat(ASK))


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
        list(role_service.gateway_for_role("agent").chat(ASK))

    totals = admin_client.get(f"{BASE}/usage").json()["totals"]
    assert totals["calls"] == 1 and totals["estimated_calls"] == 1


def test_an_abandoned_stream_is_recorded(admin_client, role_service, setup, fake_llm):
    fake_llm.script(Reply(text="one two three four", usage=Usage(50, 10)))

    stream = role_service.gateway_for_role("agent").chat(ASK, stream=True)
    next(stream)
    stream.close()

    totals = admin_client.get(f"{BASE}/usage").json()["totals"]
    assert totals["calls"] == 1 and totals["estimated_calls"] == 1
