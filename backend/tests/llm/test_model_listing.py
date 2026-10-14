"""Listing a provider's models (spec §6.18): the optional adapter capability, per adapter
(success, auth error, unsupported), and the admin endpoint that uses it."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest

from dawam.modules.llm import Gateway, LlmError
from dawam.modules.llm.gateway import ListingUnsupported
from dawam.modules.llm.internal.anthropic import AnthropicAdapter
from dawam.modules.llm.internal.azure_openai import AzureOpenAIAdapter
from dawam.modules.llm.internal.bedrock import BedrockAdapter
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.openai_compatible import OpenAICompatibleAdapter
from dawam.modules.llm.internal.transport import HttpResponse
from tests.llm.test_providers import BASE, add_model, add_provider, code

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def reply(status, body):
    return HttpResponse(status, {}, io.BytesIO(json.dumps(body).encode()))


class Script:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def request(self, method, url, *, headers, body, timeout):
        self.urls.append(url)
        return self.responses.pop(0)


def _make(kind, transport):
    common = {"timeout_seconds": 5, "transport": transport}
    if kind == "openai_compatible":
        return OpenAICompatibleAdapter(base_url="http://x/v1", api_key="k", **common)
    if kind == "anthropic":
        return AnthropicAdapter(base_url="http://x/v1", api_key="k", **common)
    if kind == "gemini":
        return GeminiAdapter(base_url="http://g/v1beta", api_key="k", **common)
    return BedrockAdapter(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key="AKID:SECRET",
        now=lambda: NOW,
        **common,
    )


OK = {
    "openai_compatible": [(200, {"data": [{"id": "a"}, {"id": "b"}]})],
    "anthropic": [
        (200, {"data": [{"id": "a"}], "has_more": True, "last_id": "a"}),
        (200, {"data": [{"id": "b"}], "has_more": False}),
    ],
    "gemini": [
        (200, {"models": [{"name": "models/a"}], "nextPageToken": "t"}),
        (200, {"models": [{"name": "models/b"}]}),
    ],
    "bedrock": [
        (200, {"modelSummaries": [{"modelId": "a"}]}),
        (200, {"inferenceProfileSummaries": [{"inferenceProfileId": "b"}]}),
    ],
}


def _ok(kind):
    return [reply(*r) for r in OK[kind]]


@pytest.mark.parametrize("kind", list(OK))
def test_an_adapter_lists_its_models(kind):
    transport = Script(*_ok(kind))

    assert _make(kind, transport).list_models() == ["a", "b"]


def test_bedrock_lists_on_the_control_plane_endpoint():
    transport = Script(*_ok("bedrock"))

    _make("bedrock", transport).list_models()

    assert transport.urls == [
        "https://bedrock.us-east-1.amazonaws.com/foundation-models",
        "https://bedrock.us-east-1.amazonaws.com/inference-profiles",
    ]


@pytest.mark.parametrize("kind", list(OK))
def test_a_rejected_key_while_listing_is_an_auth_error(kind):
    transport = Script(reply(401, {"error": {"message": "bad key"}}))

    with pytest.raises(LlmError) as raised:
        _make(kind, transport).list_models()

    assert raised.value.code == "auth"


def test_vertex_and_azure_cannot_list():
    vertex = GeminiAdapter(
        base_url="https://us-central1-aiplatform.googleapis.com/v1/projects/p",
        api_key="k",
        timeout_seconds=5,
        transport=Script(),
    )
    azure = AzureOpenAIAdapter(
        base_url="https://r.openai.azure.com", api_key="k", timeout_seconds=5, transport=Script()
    )

    for adapter in (vertex, azure):
        with pytest.raises(ListingUnsupported):
            adapter.list_models()


def test_an_adapter_without_listing_is_unsupported_through_the_gateway():
    class Plain:
        pass

    with pytest.raises(ListingUnsupported):
        Gateway(Plain(), model="m").list_models()  # type: ignore[arg-type]


# -- the admin endpoint -------------------------------------------------------------------


def test_the_endpoint_lists_models_with_a_registered_flag(admin_client, fake_llm):
    provider = add_provider(admin_client)
    add_model(admin_client, provider, name="beta", roles=())
    fake_llm.models = ["gamma", "beta", "alpha"]

    shown = admin_client.get(f"{BASE}/providers/{provider['id']}/available-models")

    assert shown.status_code == 200, shown.text
    assert shown.json() == {
        "supported": True,
        "models": [
            {"name": "alpha", "registered": False},
            {"name": "beta", "registered": True},
            {"name": "gamma", "registered": False},
        ],
        "error": None,
        "error_code": None,
    }
    assert "sk-secret-key" not in shown.text


def test_the_endpoint_reports_a_listing_failure_and_unsupported(admin_client, fake_llm):
    provider = add_provider(admin_client)
    fake_llm.models = LlmError("auth", "The provider rejected the API key.")

    failed = admin_client.get(f"{BASE}/providers/{provider['id']}/available-models").json()
    fake_llm.models = None
    unsupported = admin_client.get(f"{BASE}/providers/{provider['id']}/available-models").json()

    assert failed["supported"] is True and failed["models"] == []
    assert failed["error_code"] == "auth" and "API key" in failed["error"]
    assert unsupported["supported"] is False and unsupported["models"] == []


def test_the_endpoint_is_admin_only_and_404s_for_an_unknown_provider(
    admin_client, signed_in_client
):
    provider = add_provider(admin_client)
    missing = admin_client.get(
        f"{BASE}/providers/00000000-0000-0000-0000-000000000000/available-models"
    )

    denied = signed_in_client.get(f"{BASE}/providers/{provider['id']}/available-models")

    assert missing.status_code == 404 and code(missing) == "provider_not_found"
    assert denied.status_code == 403
