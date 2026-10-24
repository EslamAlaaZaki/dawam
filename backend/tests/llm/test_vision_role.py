"""The ``vision`` model role (#425): an admin enables a model for it and tests it like the
other roles, a Workspace owner chooses one in the Workspace's AI settings, and every call
(page images and a prompt) goes through the ``MeteredGateway``: budgets, internal-only and
usage apply. Driven through the HTTP API with the scripted fake provider (``fake_llm``);
one test goes over real HTTP to a local OpenAI-compatible stub (vLLM, Ollama).
"""

from __future__ import annotations

import base64

import pytest

from dawam.modules.llm import Image, Reply, TextDelta, Usage
from tests.llm.stub_server import StubLlmServer
from tests.llm.test_roles_budgets import BASE, add_model, code, register, role_service
from tests.llm.test_workspace_ai import register as register_on
from tests.roles import RoleClients

__all__ = ["role_service"]  # the fixture, shared

PAGE = Image("image/png", b"page-bytes")


def settings_path(roles: RoleClients) -> str:
    return f"/api/v1/workspaces/{roles.workspace_id}/ai-settings"


def choose_vision(roles: RoleClients, model: dict | None, **fields):
    owner = roles.client("owner")
    current = owner.get(settings_path(roles)).json()
    return owner.put(
        settings_path(roles),
        json={
            "internal_only": current["internal_only"],
            "data_sharing_level": current["data_sharing_level"],
            "vision_model_id": model["id"] if model else None,
            **fields,
        },
    )


@pytest.fixture
def workspace(roles: RoleClients) -> str:
    """The roles' Workspace, created before any model is registered."""
    assert roles.workspace_id
    return roles.workspace_id


def test_test_connection_of_a_vision_model_has_it_read_a_small_image(
    admin_client, fake_llm, workspace
):
    model = register(admin_client, "qwen-vl", roles=("vision",), tested=False)

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is True, tested
    assert tested["roles"] == ["vision"]
    [image] = [i for call in fake_llm.calls for m in call.messages for i in m.images]
    assert image.media_type == "image/png" and image.data.startswith(b"\x89PNG")


def test_a_model_that_cannot_read_images_fails_the_test(admin_client, fake_llm, workspace):
    fake_llm.reads_images = False
    model = register(admin_client, "text-only", roles=("vision",), tested=False)

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is False
    assert tested["test_error_code"] == "bad_request"


def test_an_owner_chooses_the_workspaces_vision_model(admin_client, fake_llm, roles, workspace):
    vision = register(admin_client, "qwen-vl", roles=("vision",))

    response = choose_vision(roles, vision)

    assert response.status_code == 200, response.text
    settings = response.json()
    assert settings["vision_model"]["name"] == "qwen-vl"
    assert [m["name"] for m in settings["choices"]["vision"]] == ["qwen-vl"]
    assert choose_vision(roles, None).json()["vision_model"] is None


def test_the_vision_model_must_be_enabled_for_the_role(admin_client, fake_llm, roles, workspace):
    agent_only = register(admin_client, "agent-only", roles=("agent",))

    response = choose_vision(roles, agent_only, agent_model_ids=[agent_only["id"]])

    assert response.status_code == 422
    assert code(response) == "invalid_model_role"
    assert response.json()["error"]["details"]["field"] == "vision_model_id"


def test_an_internal_only_workspace_takes_only_an_internal_vision_model(
    admin_client, fake_llm, roles, workspace
):
    agent = register_on(admin_client, "local-agent", internal=True)
    external = register_on(admin_client, "cloud-vl", internal=False, roles=("vision",))

    response = choose_vision(roles, external, internal_only=True, agent_model_ids=[agent["id"]])

    assert response.status_code == 422 and code(response) == "model_not_allowed"


def test_vision_calls_are_metered_under_the_vision_role(
    admin_client, role_service, fake_llm, roles, workspace
):
    vision = register(admin_client, "qwen-vl", roles=("vision",))
    assert choose_vision(roles, vision).status_code == 200
    fake_llm.script(Reply(text="# Ledger", usage=Usage(900, 12)))
    user = roles.user("owner").id

    gateway = role_service.gateway_for_role("vision", workspace_id=workspace, user_id=user)
    events = list(gateway.vision("Transcribe this page.", [PAGE]))

    assert TextDelta("# Ledger") in events
    assert fake_llm.calls[-1].messages[0].images == (PAGE,)
    [per_workspace] = admin_client.get(f"{BASE}/usage").json()["workspaces"]
    assert per_workspace["tokens_by_role"] == {"vision": 912}


def test_unreported_vision_usage_is_estimated_with_the_images(
    admin_client, role_service, fake_llm, roles, workspace
):
    vision = register(admin_client, "qwen-vl", roles=("vision",))
    choose_vision(roles, vision)
    fake_llm.script(Reply(text="ok", usage=None))

    list(role_service.gateway_for_role("vision", workspace_id=workspace).vision("Read.", [PAGE]))

    [per_workspace] = admin_client.get(f"{BASE}/usage").json()["workspaces"]
    assert per_workspace["totals"]["estimated_calls"] == 1
    assert per_workspace["totals"]["prompt_tokens"] >= 1000  # an image is not free


def test_vision_calls_stop_when_the_workspace_budget_is_used_up(
    admin_client, role_service, fake_llm, roles, workspace
):
    vision = register(admin_client, "qwen-vl", roles=("vision",))
    choose_vision(roles, vision)
    admin_client.put(f"{BASE}/budgets/workspaces/{workspace}", json={"monthly_token_budget": 100})
    fake_llm.script(Reply(text="# Ledger", usage=Usage(900, 12)))
    gateway = role_service.gateway_for_role("vision", workspace_id=workspace)
    list(gateway.vision("Read.", [PAGE]))

    with pytest.raises(Exception) as caught:
        list(gateway.vision("Read.", [PAGE]))

    assert caught.value.status_code == 429 and caught.value.code == "token_budget_exhausted"
    assert caught.value.details["scope"] == "workspace"


def test_an_internal_only_workspace_refuses_an_external_vision_model_at_call_time(
    admin_client, role_service, fake_llm, roles, workspace
):
    external = register_on(admin_client, "cloud-vl", internal=False, roles=("vision",))
    choose_vision(roles, external)
    register_on(admin_client, "local-agent", internal=True)
    with roles.app.state.engine.begin() as conn:  # made internal-only behind the settings' back
        conn.exec_driver_sql("UPDATE llm_workspace_settings SET internal_only = true")
    calls = len(fake_llm.calls)

    with pytest.raises(Exception) as caught:
        list(role_service.gateway_for_role("vision", workspace_id=workspace).vision("R", [PAGE]))

    assert caught.value.code == "external_provider_refused"
    assert len(fake_llm.calls) == calls


def test_unticking_the_vision_role_needs_no_replacement(admin_client, fake_llm, roles, workspace):
    vision = register(admin_client, "qwen-vl", roles=("vision", "agent"))
    choose_vision(roles, vision)

    response = admin_client.put(f"{BASE}/models/{vision['id']}/roles", json={"roles": ["agent"]})

    assert response.status_code == 200, response.text
    owner = roles.client("owner")
    assert owner.get(settings_path(roles)).json()["vision_model"] is None


def test_test_connection_sends_the_image_to_a_self_hosted_server_over_real_http(
    admin_client, workspace
):
    with StubLlmServer().running() as stub:
        provider = admin_client.post(
            f"{BASE}/providers",
            json={"name": "Ollama", "base_url": stub.base_url, "internal": True},
        ).json()
        model = add_model(admin_client, provider, "qwen2.5vl", roles=("vision",))

        tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

        assert tested["test_ok"] is True, tested
        sent = [
            part
            for r in stub.requests
            for m in r.get("json", {}).get("messages", [])
            if isinstance(m["content"], list)
            for part in m["content"]
        ]
        [image] = [p for p in sent if p["type"] == "image_url"]
        url = image["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]).startswith(b"\x89PNG")
