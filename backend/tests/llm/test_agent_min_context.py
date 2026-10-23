"""The agent role needs a context window of at least 32K tokens (ticket #350).

Through the HTTP API with the signed-in admin client and the scripted ``FakeAdapter``.
"""

from __future__ import annotations

from dawam.modules.llm import OllamaContext
from tests.llm.test_providers import BASE, add_model, add_provider, code


def test_a_model_with_a_known_window_below_32k_is_refused_the_agent_role(admin_client):
    model = add_model(admin_client, add_provider(admin_client), roles=(), context_window=8192)

    refused = admin_client.put(f"{BASE}/models/{model['id']}/roles", json={"roles": ["agent"]})

    assert refused.status_code == 422 and code(refused) == "context_window_too_small"
    message = refused.json()["error"]["message"]
    assert "8,192" in message and "32K" in message
    light = admin_client.put(f"{BASE}/models/{model['id']}/roles", json={"roles": ["light"]})
    assert light.status_code == 200 and light.json()["warnings"] == []


def test_a_model_with_a_window_of_32k_or_more_takes_the_agent_role_without_warnings(admin_client):
    model = add_model(admin_client, add_provider(admin_client), roles=(), context_window=32768)

    granted = admin_client.put(f"{BASE}/models/{model['id']}/roles", json={"roles": ["agent"]})

    assert granted.status_code == 200 and granted.json()["warnings"] == []


def test_an_unknown_window_is_allowed_for_the_agent_role_with_a_warning(admin_client):
    model = add_model(admin_client, add_provider(admin_client), roles=("agent",))

    assert model["context_window"] is None
    assert len(model["warnings"]) == 1 and "unknown" in model["warnings"][0]


def test_test_connection_warns_when_the_agent_models_window_is_below_32k(admin_client, fake_llm):
    fake_llm.window = 8192
    model = add_model(admin_client, add_provider(admin_client), roles=("agent",))

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    assert tested["test_ok"] is True and tested["context_window"] == 8192
    assert any("8,192" in w and "32K" in w for w in tested["warnings"])


def test_test_connection_warns_about_ollama_num_ctx_below_32k_and_how_to_raise_it(
    admin_client, fake_llm
):
    fake_llm.window = 131072
    fake_llm.ollama = OllamaContext(num_ctx=4096)
    model = add_model(admin_client, add_provider(admin_client), roles=("agent",))

    tested = admin_client.post(f"{BASE}/models/{model['id']}/test").json()

    [warning] = tested["warnings"]
    assert "num_ctx 4,096" in warning and "PARAMETER num_ctx 32768" in warning


def test_test_connection_warns_when_ollama_has_no_num_ctx_set(admin_client, fake_llm):
    fake_llm.window = 131072
    fake_llm.ollama = OllamaContext(num_ctx=None)
    model = add_model(admin_client, add_provider(admin_client), roles=("agent",))

    [warning] = admin_client.post(f"{BASE}/models/{model['id']}/test").json()["warnings"]

    assert "no num_ctx set" in warning


def test_ollama_at_32k_and_a_non_agent_model_get_no_num_ctx_warning(admin_client, fake_llm):
    fake_llm.window = 131072
    agent = add_model(admin_client, add_provider(admin_client), roles=("agent",))
    light = add_model(admin_client, add_provider(admin_client, name="Other"), roles=("light",))

    fake_llm.ollama = OllamaContext(num_ctx=2048)
    assert admin_client.post(f"{BASE}/models/{light['id']}/test").json()["warnings"] == []
    fake_llm.ollama = OllamaContext(num_ctx=32768)
    assert admin_client.post(f"{BASE}/models/{agent['id']}/test").json()["warnings"] == []
