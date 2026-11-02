# ruff: noqa: F811
"""The ordered model fallback list per role (tickets #406, #550).

Admins set the list through the HTTP API; calls go through ``RoleService.gateway_for_role``
with the scripted ``fake_llm`` behind every provider. No real model is called. A failed
model hands over to the next of the admin's list that the Workspace uses for the role.
"""

from __future__ import annotations

import pytest

from dawam.modules.llm import LlmError, Reply, TextDelta
from dawam.modules.llm.gateway import (
    AUTH,
    BAD_REQUEST,
    CONTEXT_OVERFLOW,
    INVALID_RESPONSE,
    RATE_LIMIT,
    UNAVAILABLE,
)
from dawam.modules.llm.role_service import RoleService
from dawam.modules.llm.service import ProviderService
from dawam.platform.errors import ApiError
from tests.llm.test_roles_budgets import ASK, BASE, code, register, role_service, setup  # noqa: F401
from tests.roles import RoleClients

DOWN = LlmError(UNAVAILABLE, "provider down", retry_after=0)
PRIMARY_ATTEMPTS = 4  # one try and three retries


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def waiting_roles(app, clock, fake_llm, slept) -> RoleService:
    """A ``RoleService`` whose gateways record their backoff waits in ``slept``."""
    state = app.state
    providers = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        adapters=state.services.llm_adapters,
        sleep=slept.append,
    )
    return RoleService(state.engine, providers=providers, clock=clock)


def set_fallbacks(client, role, *models):
    return client.put(f"{BASE}/fallbacks/{role}", json={"model_ids": [m["id"] for m in models]})


def model_named(admin_client, name) -> dict:
    return next(
        m
        for p in admin_client.get(f"{BASE}/providers").json()["items"]
        for m in p["models"]
        if m["name"] == name
    )


def use_agents(roles: RoleClients, workspace_id, *models, internal_only=False, light=()):
    """The Workspace's agent models, the first the default, and its light models."""
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{workspace_id}/ai-settings",
        json={
            "internal_only": internal_only,
            "data_sharing_level": "metadata",
            "agent_model_ids": [m["id"] for m in models],
            "default_agent_model_id": models[0]["id"],
            "light_model_ids": [m["id"] for m in light],
            "default_light_model_id": light[0]["id"] if light else None,
            "embedding_model_id": None,
        },
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def backup(admin_client, roles: RoleClients, setup, fake_llm) -> dict:
    """``backup``: the admin's agent fallback, one of the Workspace's agent models."""
    model = register(admin_client, "backup")
    set_fallbacks(admin_client, "agent", model)
    use_agents(roles, setup, model_named(admin_client, "big-model"), model)
    fake_llm.calls.clear()  # registering a model probes it
    return model


def ask(role_service, workspace_id):
    gateway = role_service.gateway_for_role("agent", workspace_id=workspace_id)
    return "".join(getattr(e, "text", "") for e in gateway.chat(ASK))


def text(gateway) -> str:
    return "".join(getattr(e, "text", "") for e in gateway.chat(ASK))


def models_called(fake_llm):
    return [c.model for c in fake_llm.calls]


def test_admins_set_an_ordered_list_per_role(admin_client, setup):
    one, two = register(admin_client, "one"), register(admin_client, "two")

    response = set_fallbacks(admin_client, "agent", two, one)

    assert response.status_code == 200
    roles = {r["role"]: r["model_ids"] for r in response.json()["roles"]}
    assert roles == {"agent": [two["id"], one["id"]], "light": []}
    assert admin_client.get(f"{BASE}/fallbacks").json() == response.json()


def test_a_model_not_enabled_for_the_role_is_refused(admin_client, setup):
    light_only = register(admin_client, "tiny", roles=("light",))

    response = set_fallbacks(admin_client, "agent", light_only)

    assert response.status_code == 422 and code(response) == "invalid_fallback_model"


def test_only_admins_set_fallbacks(roles: RoleClients, setup):
    assert roles.client("owner").get(f"{BASE}/fallbacks").status_code == 403


@pytest.mark.parametrize(
    "failure", [UNAVAILABLE, RATE_LIMIT, AUTH, BAD_REQUEST, CONTEXT_OVERFLOW, INVALID_RESPONSE]
)
def test_any_failure_hands_over_to_the_next_workspace_model_without_waiting(
    waiting_roles, setup, backup, fake_llm, slept, failure
):
    fake_llm.script(LlmError(failure, "it failed", retry_after=5), Reply(text="from backup"))

    gateway = waiting_roles.gateway_for_role("agent", workspace_id=setup)

    assert text(gateway) == "from backup"
    assert models_called(fake_llm) == ["big-model", "backup"]
    assert slept == []
    assert str(gateway.model_id) == backup["id"]
    assert gateway.fell_back is True


def test_the_admin_order_is_kept(admin_client, roles: RoleClients, role_service, setup, fake_llm):
    first, second = register(admin_client, "backup-1"), register(admin_client, "backup-2")
    set_fallbacks(admin_client, "agent", first, second)
    use_agents(roles, setup, model_named(admin_client, "big-model"), second, first)
    fake_llm.calls.clear()
    fake_llm.script(LlmError(AUTH, "bad key"), LlmError(AUTH, "bad key"), Reply(text="two"))

    assert ask(role_service, setup) == "two"
    assert models_called(fake_llm) == ["big-model", "backup-1", "backup-2"]


def test_a_fallback_the_workspace_does_not_use_is_skipped(
    admin_client, roles: RoleClients, role_service, setup, fake_llm
):
    outside, inside = register(admin_client, "outside"), register(admin_client, "inside")
    set_fallbacks(admin_client, "agent", outside, inside)
    use_agents(roles, setup, model_named(admin_client, "big-model"), inside)
    fake_llm.calls.clear()
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="from inside"))

    assert ask(role_service, setup) == "from inside"
    assert models_called(fake_llm) == ["big-model", "inside"]


def test_without_candidates_the_original_is_retried_with_backoff(
    admin_client, waiting_roles, setup, fake_llm, slept
):
    outside = register(admin_client, "outside")  # not one of the Workspace's models
    set_fallbacks(admin_client, "agent", outside)
    fake_llm.calls.clear()
    fake_llm.script(*[LlmError(UNAVAILABLE, "down")] * 3, Reply(text="at last"))

    assert ask(waiting_roles, setup) == "at last"
    assert models_called(fake_llm) == ["big-model"] * PRIMARY_ATTEMPTS
    assert len(slept) == 3


def test_a_budget_refusal_does_not_fall_back(admin_client, role_service, setup, backup, fake_llm):
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 1})
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))  # uses it up
    fake_llm.calls.clear()

    with pytest.raises(ApiError) as caught:
        ask(role_service, setup)

    assert caught.value.code == "token_budget_exhausted"
    assert fake_llm.calls == []


def test_an_internal_only_refusal_does_not_fall_back(
    admin_client, role_service, roles: RoleClients, setup, fake_llm
):
    provider = admin_client.post(
        f"{BASE}/providers",
        json={"name": "Local", "base_url": "http://llm.test/v1", "internal": True},
    ).json()
    primary = register(admin_client, "local-primary", provider=provider)
    backup = register(admin_client, "local-backup")
    set_fallbacks(admin_client, "agent", backup)
    use_agents(roles, setup, primary, backup, internal_only=True)
    admin_client.put(
        f"{BASE}/providers/{provider['id']}",
        json={"name": "Local", "base_url": "http://llm.test/v1", "internal": False},
    )
    fake_llm.calls.clear()

    with pytest.raises(ApiError) as caught:
        ask(role_service, setup)

    assert caught.value.code == "external_provider_refused"
    assert fake_llm.calls == []


def test_a_reply_that_breaks_after_its_first_event_does_not_fall_back(
    role_service, setup, backup, fake_llm, monkeypatch
):
    calls: list[str] = []

    def recorded(model, messages, tools, stream, json_schema, reasoning=None):
        calls.append(model)
        yield TextDelta("half an answer")
        raise LlmError(UNAVAILABLE, "the stream broke")

    monkeypatch.setattr(fake_llm, "chat", recorded)

    with pytest.raises(LlmError) as caught:
        ask(role_service, setup)

    assert caught.value.message == "the stream broke"
    assert calls == ["big-model"]


def test_after_a_fallback_answers_the_run_stays_on_it(role_service, setup, backup, fake_llm):
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="one"), Reply(text="two"))
    gateway = role_service.gateway_for_role("agent", workspace_id=setup)

    assert text(gateway) == "one"
    assert text(gateway) == "two"
    assert models_called(fake_llm) == ["big-model", "backup", "backup"]


def test_when_every_model_fails_the_error_names_each(role_service, setup, backup, fake_llm):
    fake_llm.script(LlmError(AUTH, "bad key"), LlmError(BAD_REQUEST, "refused the request"))

    with pytest.raises(LlmError) as caught:
        ask(role_service, setup)

    message = caught.value.message
    assert "big-model" in message and "bad key" in message
    assert "backup" in message and "refused the request" in message


def test_a_single_model_failing_keeps_its_own_error(role_service, setup, fake_llm):
    fake_llm.script(LlmError(AUTH, "bad key"))

    with pytest.raises(LlmError) as caught:
        ask(role_service, setup)

    assert caught.value.code == AUTH and caught.value.message == "bad key"


def test_a_picked_model_falls_back_too(admin_client, role_service, setup, backup, fake_llm):
    picked = model_named(admin_client, "big-model")
    fake_llm.script(LlmError(AUTH, "bad key"), Reply(text="from backup"))
    gateway = role_service.metered_gateway(
        picked["id"], "agent", workspace_id=setup, with_fallbacks=True
    )

    assert text(gateway) == "from backup"
    assert gateway.fell_back is True


def test_light_features_fall_back_within_the_workspace_light_models(
    admin_client, roles: RoleClients, role_service, setup, fake_llm
):
    tiny = register(admin_client, "tiny", roles=("light",))
    spare = register(admin_client, "spare", roles=("light",))
    set_fallbacks(admin_client, "light", spare)
    use_agents(roles, setup, model_named(admin_client, "big-model"), light=(tiny, spare))
    fake_llm.calls.clear()
    fake_llm.script(LlmError(RATE_LIMIT, "slow down"), Reply(text="titled"))

    gateway = role_service.light_gateway(workspace_id=setup)

    assert gateway is not None and text(gateway) == "titled"
    assert models_called(fake_llm) == ["tiny", "spare"]


def test_an_external_fallback_is_skipped_for_an_internal_only_workspace(
    admin_client,
    role_service,
    roles: RoleClients,
    setup,
    fake_llm,
):
    external_provider = admin_client.post(
        f"{BASE}/providers",
        json={"name": "Cloud", "base_url": "http://cloud.test/v1", "internal": False},
    ).json()
    external = register(admin_client, "cloud-backup", provider=external_provider)
    internal = register(admin_client, "local-backup")
    set_fallbacks(admin_client, "agent", external, internal)
    use_agents(roles, setup, model_named(admin_client, "big-model"), internal, internal_only=True)
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(DOWN, Reply(text="stayed internal"))

    assert ask(role_service, setup) == "stayed internal"
    assert models_called(fake_llm) == ["big-model", "local-backup"]
