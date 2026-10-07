"""LLM providers and models in the admin console (spec §6.18, stories 155, 156, 158).

Driven through the HTTP API with the scripted fake provider behind every provider
(``fake_llm``). Two deliberate storage-property checks read the module's own table: the
API key is stored sealed. One test goes through the real HTTP transport against a local
OpenAI-compatible stub server.
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from dawam.modules.llm import LlmError, ProviderService
from dawam.platform.crypto import SecretBox
from tests.llm.stub_server import StubLlmServer

BASE = "/api/v1/admin/llm"


def provider_body(**overrides):
    return {
        "name": "Local vLLM",
        "base_url": "http://vllm:8000/v1",
        "api_key": "sk-secret-key",
        "internal": True,
        **overrides,
    }


def add_provider(client, **overrides) -> dict:
    response = client.post(f"{BASE}/providers", json=provider_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def add_model(client, provider, name="qwen3", roles=("agent",), **extra) -> dict:
    response = client.post(
        f"{BASE}/providers/{provider['id']}/models",
        json={"name": name, "roles": list(roles), **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def code(response) -> str:
    return response.json()["error"]["code"]


def test_an_admin_registers_a_provider_and_the_key_is_never_returned(admin_client, app):
    response = admin_client.post(f"{BASE}/providers", json=provider_body())

    assert response.status_code == 201, response.text
    created = response.json()
    assert created["has_api_key"] is True and created["internal"] is True
    assert created["adapter"] == "openai_compatible" and created["models"] == []
    assert "sk-secret-key" not in response.text and "api_key" not in created
    for shown in (
        admin_client.get(f"{BASE}/providers/{created['id']}"),
        admin_client.get(f"{BASE}/providers"),
    ):
        assert "sk-secret-key" not in shown.text

    with app.state.engine.connect() as conn:
        sealed = conn.scalar(sa.text("SELECT secret_encrypted FROM llm_providers"))
    assert "sk-secret-key" not in sealed
    box = SecretBox(app.state.settings.encryption_key.get_secret_value())
    assert box.decrypt(sealed, context="llm.provider.api_key") == "sk-secret-key"


def test_an_admin_registers_claude_through_the_anthropic_adapter(admin_client, fake_llm):
    created = add_provider(
        admin_client,
        name="Claude",
        adapter="anthropic",
        base_url="https://api.anthropic.com/v1",
        internal=False,
    )
    model = add_model(admin_client, created, name="claude-sonnet-4-5")

    assert created["adapter"] == "anthropic"
    assert admin_client.get(f"{BASE}/providers/{created['id']}").json()["adapter"] == "anthropic"
    tested = admin_client.post(f"{BASE}/models/{model['id']}/test")
    assert tested.status_code == 200, tested.text


def test_an_azure_provider_keeps_its_api_version_and_the_adapter_receives_it(
    admin_client, fake_llm, services
):
    seen = []

    def adapters(kind, config):
        seen.append((kind, config.base_url))
        return fake_llm

    services.llm_adapters = adapters
    url = "https://res.openai.azure.com?api-version=2025-01-01-preview"
    created = add_provider(admin_client, adapter="azure_openai", base_url=url, internal=False)
    model = add_model(admin_client, created, name="my-deployment")

    assert created["base_url"] == url
    assert admin_client.post(f"{BASE}/models/{model['id']}/test").status_code == 200
    assert ("azure_openai", url) in seen


def test_only_azure_may_carry_an_api_version_and_nothing_else_in_the_query(admin_client):
    url = "https://res.openai.azure.com?api-version=2025-01-01-preview"
    for overrides in (
        {"adapter": "openai_compatible", "base_url": url},
        {"adapter": "azure_openai", "base_url": url + "&x=1"},
        {"adapter": "azure_openai", "base_url": "https://res.openai.azure.com?foo=1"},
    ):
        response = admin_client.post(f"{BASE}/providers", json=provider_body(**overrides))
        assert response.status_code == 422, (overrides, response.text)


def test_a_provider_without_a_key_reports_no_key(admin_client):
    created = add_provider(admin_client, api_key=None, name="Ollama")

    assert created["has_api_key"] is False


def test_every_provider_is_listed_flagged_internal_or_external(admin_client):
    add_provider(admin_client, name="Local vLLM", internal=True)
    add_provider(admin_client, name="OpenAI", base_url="https://api.openai.com/v1", internal=False)

    items = admin_client.get(f"{BASE}/providers").json()["items"]

    assert [(p["name"], p["internal"]) for p in items] == [("Local vLLM", True), ("OpenAI", False)]


def test_updating_keeps_the_key_unless_the_base_url_changes(admin_client, app, fake_llm):
    provider = add_provider(admin_client)
    body = provider_body(api_key=None, name="Renamed", internal=False, timeout_seconds=90)

    kept = admin_client.put(f"{BASE}/providers/{provider['id']}", json=body)

    assert kept.status_code == 200, kept.text
    assert kept.json()["has_api_key"] is True and kept.json()["name"] == "Renamed"
    assert kept.json()["internal"] is False and kept.json()["timeout_seconds"] == 90

    moved = admin_client.put(
        f"{BASE}/providers/{provider['id']}", json={**body, "base_url": "http://elsewhere/v1"}
    )
    assert moved.status_code == 422 and code(moved) == "api_key_required"

    cleared = admin_client.put(f"{BASE}/providers/{provider['id']}", json={**body, "api_key": ""})
    assert cleared.json()["has_api_key"] is False


def test_provider_names_are_unique(admin_client):
    add_provider(admin_client)

    response = admin_client.post(f"{BASE}/providers", json=provider_body())

    assert response.status_code == 409 and code(response) == "provider_name_taken"


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": "ftp://llm/v1"},
        {"base_url": "not a url"},
        {"base_url": "http://user:pw@llm/v1"},
        {"name": "  "},
        {"timeout_seconds": 0},
        {"adapter": "cohere"},
    ],
)
def test_bad_provider_settings_are_rejected(admin_client, overrides):
    response = admin_client.post(f"{BASE}/providers", json=provider_body(**overrides))

    assert response.status_code == 422


def test_models_are_added_edited_and_removed(admin_client):
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider, roles=("agent",), context_window=8192)
    assert model["test_ok"] is None and model["context_window"] == 8192

    edited = admin_client.put(
        f"{BASE}/models/{model['id']}", json={"name": "qwen3-14b", "roles": ["agent", "light"]}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["roles"] == ["agent", "light"] and edited.json()["context_window"] is None
    listed = admin_client.get(f"{BASE}/providers/{provider['id']}").json()["models"]
    assert [m["name"] for m in listed] == ["qwen3-14b"]

    assert admin_client.delete(f"{BASE}/models/{model['id']}").status_code == 204
    assert admin_client.get(f"{BASE}/providers/{provider['id']}").json()["models"] == []


def test_model_names_are_unique_per_provider_and_roles_are_checked(admin_client):
    provider = add_provider(admin_client)
    add_model(admin_client, provider, name="qwen3")

    duplicate = admin_client.post(
        f"{BASE}/providers/{provider['id']}/models", json={"name": "qwen3", "roles": ["light"]}
    )
    both = admin_client.post(
        f"{BASE}/providers/{provider['id']}/models",
        json={"name": "bge", "roles": ["agent", "embedding"]},
    )

    assert duplicate.status_code == 409 and code(duplicate) == "model_exists"
    assert both.status_code == 422 and code(both) == "invalid_llm_config"


def test_deleting_a_provider_removes_its_models(admin_client):
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider)

    assert admin_client.delete(f"{BASE}/providers/{provider['id']}").status_code == 204

    assert admin_client.get(f"{BASE}/providers/{provider['id']}").status_code == 404
    gone = admin_client.post(f"{BASE}/models/{model['id']}/test")
    assert gone.status_code == 404 and code(gone) == "model_not_found"


def test_test_connection_records_capabilities_and_context_window(admin_client, fake_llm):
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider)

    response = admin_client.post(f"{BASE}/models/{model['id']}/test")

    assert response.status_code == 200, response.text
    tested = response.json()
    assert tested["test_ok"] is True and tested["test_error"] is None
    assert (tested["tool_calling"], tested["streaming"], tested["json_schema"]) == (
        True,
        True,
        True,
    )
    assert tested["context_window"] == 8192 and tested["limited"] is False
    assert tested["last_tested_at"] is not None
    tool_probe = [c for c in fake_llm.calls if c.tools]
    assert tool_probe and tool_probe[0].model == "qwen3"


def test_a_model_without_tool_calling_is_shown_as_limited(admin_client, fake_llm):
    fake_llm.supports_tools = False
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider, context_window=4096)

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is True and tested["tool_calling"] is False
    assert tested["limited"] is True and tested["context_window"] == 4096


def test_test_connection_of_an_embedding_model_records_its_dimension(admin_client, fake_llm):
    fake_llm.dimension = 768
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider, name="bge-m3", roles=("embedding",))

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is True and tested["embedding_dimension"] == 768
    assert tested["tool_calling"] is None


def test_a_failed_test_is_recorded_with_the_common_error_code(admin_client, fake_llm):
    fake_llm.script(LlmError("auth", "The provider rejected the API key."))
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider)

    response = admin_client.post(f"{BASE}/models/{model['id']}/test")

    assert response.status_code == 200
    failed = response.json()
    assert failed["test_ok"] is False and failed["test_error_code"] == "auth"
    assert failed["test_error"] == "The provider rejected the API key."


def test_changing_the_base_url_drops_the_test_results(admin_client, fake_llm):
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider)
    admin_client.post(f"{BASE}/models/{model['id']}/test")

    moved = admin_client.put(
        f"{BASE}/providers/{provider['id']}",
        json=provider_body(base_url="http://elsewhere/v1", api_key="sk-new"),
    )

    assert moved.json()["models"][0]["test_ok"] is None
    assert moved.json()["models"][0]["tool_calling"] is None


def test_setup_is_incomplete_until_an_agent_model_is_tested(admin_client, fake_llm):
    def setup() -> dict:
        return admin_client.get(f"{BASE}/setup").json()

    assert setup() == {
        "complete": False,
        "has_agent_model": False,
        "has_tested_agent_model": False,
        "has_internal_agent_model": False,
    }
    external = add_provider(admin_client, name="OpenAI", internal=False)
    light = add_model(admin_client, external, name="mini", roles=("light",))
    admin_client.post(f"{BASE}/models/{light['id']}/test")
    agent = add_model(admin_client, external, name="gpt", roles=("agent",))
    assert setup()["has_agent_model"] is True and setup()["complete"] is False

    admin_client.post(f"{BASE}/models/{agent['id']}/test")
    assert setup() == {
        "complete": True,
        "has_agent_model": True,
        "has_tested_agent_model": True,
        "has_internal_agent_model": False,
    }

    internal = add_provider(admin_client, name="vLLM", internal=True)
    local = add_model(admin_client, internal, roles=("agent",))
    admin_client.post(f"{BASE}/models/{local['id']}/test")
    assert setup()["has_internal_agent_model"] is True


def test_the_gateway_of_a_registered_model_carries_what_its_test_found(admin_client, app, fake_llm):
    provider = add_provider(admin_client)
    model = add_model(admin_client, provider)
    admin_client.post(f"{BASE}/models/{model['id']}/test")
    state = app.state
    service = ProviderService(
        state.engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=state.services.clock,
        adapters=state.services.llm_adapters,
    )
    gateway = service.gateway_for(uuid.UUID(model["id"]))

    assert gateway.capabilities().tool_calling is True
    assert gateway.capabilities().context_window == 8192


def test_test_connection_works_over_real_http_against_an_openai_compatible_server(admin_client):
    with StubLlmServer().running() as stub:
        provider = add_provider(admin_client, base_url=stub.base_url, api_key="sk-stub")
        model = add_model(admin_client, provider, name="stub-model")

        tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

        assert tested["test_ok"] is True, tested
        assert (tested["tool_calling"], tested["streaming"], tested["json_schema"]) == (
            True,
            True,
            True,
        )
        assert tested["context_window"] == 16384
        assert {r["auth"] for r in stub.requests} == {"Bearer sk-stub"}
        assert any(r["path"] == "/v1/chat/completions" for r in stub.requests)

        stub.fail_with = 401
        failed = admin_client.post(f"{BASE}/models/{model['id']}/test").json()
        assert failed["test_ok"] is False and failed["test_error_code"] == "auth"
        assert "sk-stub" not in json.dumps(failed)


def test_an_unreachable_server_is_reported_not_raised(admin_client):
    provider = add_provider(admin_client, base_url="http://127.0.0.1:1/v1", timeout_seconds=1)
    model = add_model(admin_client, provider)

    failed = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert failed["test_ok"] is False and failed["test_error_code"] == "unavailable"
