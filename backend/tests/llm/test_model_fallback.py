# ruff: noqa: F811
"""The ordered model fallback list per role (ticket #406).

Admins set the list through the HTTP API; calls go through ``RoleService.gateway_for_role``
with the scripted ``fake_llm`` behind every provider. No real model is called.
"""

from __future__ import annotations

import pytest

from dawam.modules.llm import LlmError, Reply
from dawam.modules.llm.gateway import AUTH, UNAVAILABLE
from tests.llm.test_roles_budgets import ASK, BASE, code, register, role_service, setup  # noqa: F401
from tests.roles import RoleClients

DOWN = LlmError(UNAVAILABLE, "provider down", retry_after=0)
PRIMARY_ATTEMPTS = 4  # one try and three retries


def set_fallbacks(client, role, *models):
    return client.put(f"{BASE}/fallbacks/{role}", json={"model_ids": [m["id"] for m in models]})


def ask(role_service, workspace_id):
    gateway = role_service.gateway_for_role("agent", workspace_id=workspace_id)
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


def test_an_unavailable_model_falls_back_in_order(
    admin_client,
    role_service,
    setup,
    fake_llm,
):
    first, second = register(admin_client, "backup-1"), register(admin_client, "backup-2")
    set_fallbacks(admin_client, "agent", first, second)
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(*[DOWN] * PRIMARY_ATTEMPTS, *[DOWN] * PRIMARY_ATTEMPTS, Reply(text="from two"))

    assert ask(role_service, setup) == "from two"
    called = models_called(fake_llm)
    assert called[0] == "big-model" and called[-1] == "backup-2"
    assert "backup-1" in called


def test_a_timeout_is_unavailable_and_falls_back(
    admin_client,
    role_service,
    setup,
    fake_llm,
):
    backup = register(admin_client, "backup")
    set_fallbacks(admin_client, "agent", backup)
    timeout = LlmError(UNAVAILABLE, "The provider did not answer in time.", retry_after=0)
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(*[timeout] * PRIMARY_ATTEMPTS, Reply(text="late but fine"))

    assert ask(role_service, setup) == "late but fine"


def test_other_failures_do_not_fall_back(admin_client, role_service, setup, fake_llm):
    backup = register(admin_client, "backup")
    set_fallbacks(admin_client, "agent", backup)
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(LlmError(AUTH, "bad key"))

    with pytest.raises(LlmError):
        ask(role_service, setup)
    assert models_called(fake_llm) == ["big-model"]


def test_a_budget_refusal_does_not_fall_back(
    admin_client,
    role_service,
    setup,
    fake_llm,
):
    backup = register(admin_client, "backup")
    set_fallbacks(admin_client, "agent", backup)
    admin_client.put(f"{BASE}/budgets/installation", json={"monthly_token_budget": 1})
    list(role_service.gateway_for_role("agent", workspace_id=setup).chat(ASK))  # uses it up
    fake_llm.calls.clear()

    with pytest.raises(Exception) as caught:
        ask(role_service, setup)

    assert caught.value.code == "token_budget_exhausted"
    assert fake_llm.calls == []


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
    primary = next(
        m
        for p in admin_client.get(f"{BASE}/providers").json()["items"]
        for m in p["models"]
        if m["name"] == "big-model"
    )
    response = roles.client("owner").put(
        f"/api/v1/workspaces/{setup}/ai-settings",
        json={
            "internal_only": True,
            "data_sharing_level": "metadata",
            "agent_model_ids": [primary["id"]],
            "light_model_ids": [],
            "embedding_model_id": None,
        },
    )
    assert response.status_code == 200, response.text
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(*[DOWN] * PRIMARY_ATTEMPTS, Reply(text="stayed internal"))

    assert ask(role_service, setup) == "stayed internal"
    assert "cloud-backup" not in models_called(fake_llm)
    assert models_called(fake_llm)[-1] == "local-backup"


def test_the_last_failure_is_raised_when_every_model_is_unavailable(
    admin_client,
    role_service,
    setup,
    fake_llm,
):
    backup = register(admin_client, "backup")
    set_fallbacks(admin_client, "agent", backup)
    fake_llm.calls.clear()  # registering a model probes it
    fake_llm.script(*[DOWN] * (2 * PRIMARY_ATTEMPTS))

    with pytest.raises(LlmError) as caught:
        ask(role_service, setup)
    assert caught.value.code == UNAVAILABLE
