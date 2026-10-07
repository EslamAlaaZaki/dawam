"""What is specific to the Gemini and Bedrock adapters (shared behaviour: contract suite)."""

from __future__ import annotations

import base64
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from dawam.modules.llm import (
    AdapterConfig,
    LlmError,
    Message,
    TextDelta,
    ToolCallEvent,
    adapter_for,
)
from dawam.modules.llm.internal.bedrock import AwsCredentials, BedrockAdapter, sign_v4
from dawam.modules.llm.internal.gemini import GeminiAdapter
from dawam.modules.llm.internal.transport import HttpResponse
from tests.llm.replay import ReplayTransport, eventstream_frame, load

FIXTURES = Path(__file__).parent / "fixtures"
ASK = [Message("user", "Hi")]
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def chat(adapter, **kwargs):
    return list(
        adapter.chat("test-model", ASK, kwargs.get("tools", []), kwargs.get("stream", False), None)
    )


def reply(status, body, headers=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(status, headers or {}, io.BytesIO(raw))


class Queue:
    """A fake transport that answers by URL with queued responses."""

    def __init__(self, **by_host):
        self.by_host = {host: list(responses) for host, responses in by_host.items()}
        self.calls = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((url, dict(headers), body))
        for host, responses in self.by_host.items():
            if host in url:
                return responses.pop(0)
        raise AssertionError(f"unexpected request to {url}")


# -- registry ----------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "cls"), [("gemini", GeminiAdapter), ("bedrock", BedrockAdapter)])
def test_the_registry_builds_both_adapters(kind, cls):
    url = (
        "https://bedrock-runtime.eu-west-1.amazonaws.com" if kind == "bedrock" else "https://g.test"
    )

    adapter = adapter_for(kind, AdapterConfig(url, "AK:SK", 5), ReplayTransport())

    assert isinstance(adapter, cls)


# -- Gemini ------------------------------------------------------------------------------


def test_gemini_answers_a_tool_call_without_an_id_with_a_generated_one():
    body = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "echo", "args": {}}}],
                },
                "finishReason": "STOP",
            }
        ]
    }
    adapter = GeminiAdapter(
        base_url="https://g.test",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"g.test": [reply(200, body)]}),
    )

    event = chat(adapter)[0]

    assert isinstance(event, ToolCallEvent) and event.call.id.startswith("call_")


def test_gemini_reports_a_wrong_api_key_as_auth_even_though_it_answers_400():
    error = {"error": {"code": 400, "message": "API key not valid.", "status": "INVALID_ARGUMENT"}}
    adapter = GeminiAdapter(
        base_url="https://g.test",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"g.test": [reply(400, error)]}),
    )

    with pytest.raises(LlmError) as raised:
        chat(adapter)

    assert raised.value.code == "auth"


def test_gemini_reports_a_blocked_prompt_as_a_bad_request():
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
    adapter = GeminiAdapter(
        base_url="https://g.test",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"g.test": [reply(200, blocked)]}),
    )

    with pytest.raises(LlmError) as raised:
        chat(adapter)

    assert raised.value.code == "bad_request"


def test_gemini_keeps_thoughts_out_of_the_reply_and_counts_them_as_output():
    body = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "hmm", "thought": True}, {"text": "Answer."}],
                },
                "finishReason": "MAX_TOKENS",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 5,
            "candidatesTokenCount": 2,
            "thoughtsTokenCount": 7,
        },
    }
    adapter = GeminiAdapter(
        base_url="https://g.test",
        api_key="k",
        timeout_seconds=9,
        transport=Queue(**{"g.test": [reply(200, body)]}),
    )

    events = chat(adapter)

    assert events[0] == TextDelta("Answer.")
    assert (events[-1].usage.prompt_tokens, events[-1].usage.completion_tokens) == (5, 9)
    assert events[-1].finish_reason == "length"


def test_gemini_sends_structured_output_as_a_response_schema():
    transport = ReplayTransport(load(FIXTURES / "gemini", "chat_text"))
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key="k", timeout_seconds=9, transport=transport
    )
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}

    list(adapter.chat("test-model", ASK, [], False, schema))

    assert transport.requests[0].json["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }


@pytest.fixture
def service_account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    credential = json.dumps(
        {
            "type": "service_account",
            "client_email": "bot@proj.iam.gserviceaccount.com",
            "private_key": pem,
            "token_uri": "https://oauth2.test/token",
        }
    )
    return credential, key.public_key(), pem


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def test_a_service_account_signs_a_jwt_trades_it_for_a_token_and_caches_it(service_account):
    credential, public_key, _ = service_account
    ok = {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}]}
    transport = Queue(
        **{
            "oauth2.test": [reply(200, {"access_token": "tok-1", "expires_in": 3600})],
            "g.test": [reply(200, ok), reply(200, ok)],
        }
    )
    adapter = GeminiAdapter(
        base_url="https://g.test/v1beta", api_key=credential, timeout_seconds=9, transport=transport
    )

    chat(adapter)
    chat(adapter)

    token_call, first, second = transport.calls[0], transport.calls[1], transport.calls[2]
    form = parse_qs(token_call[2].decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    header, claims, signature = form["assertion"][0].split(".")
    assert json.loads(_b64d(header)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64d(claims))
    assert claims["iss"] == "bot@proj.iam.gserviceaccount.com"
    assert claims["aud"] == "https://oauth2.test/token"
    assert claims["exp"] - claims["iat"] == 3600
    public_key.verify(
        _b64d(signature),
        f"{form['assertion'][0].rsplit('.', 1)[0]}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert first[1]["Authorization"] == second[1]["Authorization"] == "Bearer tok-1"
    assert "x-goog-api-key" not in first[1]
    assert len(transport.calls) == 3  # one token request, two chats


def test_a_service_account_token_is_renewed_after_a_401_and_never_leaks(service_account):
    credential, _, _ = service_account
    ok = {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}]}
    transport = Queue(
        **{
            "oauth2.test": [
                reply(200, {"access_token": "tok-1", "expires_in": 3600}),
                reply(200, {"access_token": "tok-2", "expires_in": 3600}),
            ],
            "g.test": [reply(401, {"error": {"message": "bad tok-1"}}), reply(200, ok)],
        }
    )
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key=credential, timeout_seconds=9, transport=transport
    )

    assert chat(adapter)[0] == TextDelta("x")
    assert transport.calls[-1][1]["Authorization"] == "Bearer tok-2"

    failing = Queue(
        **{
            "oauth2.test": [
                reply(200, {"access_token": "tok-1", "expires_in": 3600}) for _ in range(2)
            ],
            "g.test": [reply(401, {"error": {"message": "bad tok-1"}}) for _ in range(2)],
        }
    )
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key=credential, timeout_seconds=9, transport=failing
    )
    with pytest.raises(LlmError) as raised:
        chat(adapter)
    assert "tok-1" not in raised.value.message


@pytest.mark.parametrize("credential", ['{"not": "a key"}', "{broken"])
def test_a_malformed_service_account_key_is_an_auth_error(credential):
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key=credential, timeout_seconds=9, transport=Queue()
    )

    with pytest.raises(LlmError) as raised:
        chat(adapter)

    assert raised.value.code == "auth"


def test_a_rejected_service_account_is_an_auth_error(service_account):
    credential, _, _ = service_account
    transport = Queue(**{"oauth2.test": [reply(400, {"error": "invalid_grant"})]})
    adapter = GeminiAdapter(
        base_url="https://g.test", api_key=credential, timeout_seconds=9, transport=transport
    )

    with pytest.raises(LlmError) as raised:
        chat(adapter)

    assert raised.value.code == "auth"


def test_vertex_ai_uses_the_publisher_path_of_its_base_url():
    transport = ReplayTransport(load(FIXTURES / "gemini", "chat_text"))
    base = "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/publishers/google"
    adapter = GeminiAdapter(
        base_url=base,
        api_key=None,
        timeout_seconds=9,
        transport=transport,
        token_provider=lambda: "t",
    )

    chat(adapter)

    assert transport.requests[0].url == f"{base}/models/test-model:generateContent"
    assert transport.requests[0].headers["Authorization"] == "Bearer t"


# -- Bedrock -----------------------------------------------------------------------------


def test_sigv4_matches_the_published_aws_test_vector():
    signed = sign_v4(
        "GET",
        "https://example.amazonaws.com/",
        {},
        b"",
        credentials=AwsCredentials("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"),
        region="us-east-1",
        service="service",
        now=datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC),
    )

    assert signed["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-date, "
        "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
    )


def _bedrock(transport, credential="AKID:SECRET", **extra):
    return BedrockAdapter(
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        api_key=credential,
        timeout_seconds=9,
        transport=transport,
        now=lambda: NOW,
        **extra,
    )


def test_bedrock_signs_with_the_session_token_and_double_encodes_the_model_id_path():
    transport = ReplayTransport(load(FIXTURES / "bedrock", "chat_text"))
    adapter = _bedrock(transport, "AKID:SECRET:SESSION")

    list(adapter.chat("anthropic.claude-3:0", ASK, [], False, None))

    request = transport.requests[0]
    assert request.url.endswith("/model/anthropic.claude-3%3A0/converse")
    assert request.headers["X-Amz-Security-Token"] == "SESSION"
    assert (
        "SignedHeaders=accept;content-type;host;x-amz-date;x-amz-security-token,"
        in (request.headers["Authorization"])
    )
    assert "SECRET" not in request.headers["Authorization"]


def test_bedrock_needs_a_regional_endpoint_and_a_credential():
    odd = BedrockAdapter(
        base_url="https://llm.test", api_key="A:B", timeout_seconds=9, transport=ReplayTransport()
    )
    with pytest.raises(LlmError) as raised:
        chat(odd)
    assert raised.value.code == "bad_request"

    for credential in (None, "only-one-field"):
        with pytest.raises(LlmError) as raised:
            chat(_bedrock(ReplayTransport(), credential))
        assert raised.value.code == "auth"


def test_bedrock_sends_structured_output_as_a_json_schema_format():
    transport = ReplayTransport(load(FIXTURES / "bedrock", "chat_text"))
    schema = {"type": "object"}

    list(_bedrock(transport).chat("m", ASK, [], False, schema))

    sent = transport.requests[0].json["outputConfig"]["textFormat"]
    assert sent["type"] == "json_schema"
    assert json.loads(sent["structure"]["jsonSchema"]["schema"]) == schema


def test_bedrock_assumes_a_role_and_signs_with_its_temporary_credentials():
    sts = {
        "AssumeRoleResponse": {
            "AssumeRoleResult": {
                "Credentials": {
                    "AccessKeyId": "ASIAROLE",
                    "SecretAccessKey": "role-secret",
                    "SessionToken": "role-token",
                    "Expiration": 1767323045,
                }
            }
        }
    }
    ok = json.loads((FIXTURES / "bedrock" / "chat_text.json").read_text())["body"]
    transport = Queue(
        **{"sts.us-east-1": [reply(200, sts)], "bedrock-runtime": [reply(200, ok), reply(200, ok)]}
    )
    adapter = _bedrock(transport, "AKID:SECRET;role=arn:aws:iam::123456789012:role/dawam")

    chat(adapter)
    chat(adapter)

    sts_url, sts_headers, sts_body = transport.calls[0]
    assert sts_url == "https://sts.us-east-1.amazonaws.com/"
    form = parse_qs(sts_body.decode())
    assert form["Action"] == ["AssumeRole"]
    assert form["RoleArn"] == ["arn:aws:iam::123456789012:role/dawam"]
    assert "Credential=AKID/20260102/us-east-1/sts/aws4_request" in sts_headers["Authorization"]
    for _, headers, _ in transport.calls[1:]:
        assert (
            "Credential=ASIAROLE/20260102/us-east-1/bedrock/aws4_request"
            in headers["Authorization"]
        )
        assert headers["X-Amz-Security-Token"] == "role-token"
    assert len(transport.calls) == 3  # one AssumeRole, two chats


def test_bedrock_renews_role_credentials_after_a_403_and_a_rejected_role_is_auth():
    def sts(n):
        creds = {"AccessKeyId": f"ASIA{n}", "SecretAccessKey": "s", "SessionToken": f"t{n}"}
        return reply(200, {"AssumeRoleResponse": {"AssumeRoleResult": {"Credentials": creds}}})

    ok = json.loads((FIXTURES / "bedrock" / "chat_text.json").read_text())["body"]
    transport = Queue(
        **{
            "sts.": [sts(1), sts(2)],
            "bedrock-runtime": [reply(403, {"message": "expired"}), reply(200, ok)],
        }
    )

    chat(_bedrock(transport, "A:B;role=arn:r"))

    assert "Credential=ASIA2/" in transport.calls[-1][1]["Authorization"]

    denied = Queue(**{"sts.": [reply(403, {"message": "not authorized"})]})
    with pytest.raises(LlmError) as raised:
        chat(_bedrock(denied, "A:B;role=arn:r"))
    assert raised.value.code == "auth"


def _stream(*frames):
    return HttpResponse(200, {}, io.BytesIO(b"".join(frames)))


def test_a_bedrock_exception_mid_stream_is_mapped_and_a_truncated_stream_is_unavailable():
    start = eventstream_frame("messageStart", {"role": "assistant"})
    throttled = eventstream_frame("throttlingException", {"message": "slow down"}, "exception")

    with pytest.raises(LlmError) as raised:
        chat(_bedrock(ReplayTransport(_stream(start, throttled))), stream=True)
    assert raised.value.code == "rate_limit"

    with pytest.raises(LlmError) as raised:
        chat(_bedrock(ReplayTransport(_stream(start))), stream=True)
    assert raised.value.code == "unavailable"

    with pytest.raises(LlmError) as raised:
        chat(_bedrock(ReplayTransport(_stream(start[:-2]))), stream=True)
    assert raised.value.code == "invalid_response"


def test_a_corrupt_bedrock_frame_is_an_invalid_response():
    frame = bytearray(eventstream_frame("messageStart", {"role": "assistant"}))
    frame[-6] ^= 0xFF

    with pytest.raises(LlmError) as raised:
        chat(_bedrock(ReplayTransport(_stream(bytes(frame)))), stream=True)

    assert raised.value.code == "invalid_response"


def test_bedrock_counts_cached_input_as_prompt_tokens():
    body = {
        "output": {"message": {"role": "assistant", "content": [{"text": "x"}]}},
        "stopReason": "end_turn",
        "usage": {
            "inputTokens": 5,
            "outputTokens": 2,
            "cacheReadInputTokens": 20,
            "cacheWriteInputTokens": 3,
        },
    }

    events = chat(_bedrock(ReplayTransport(reply(200, body))))

    assert (events[-1].usage.prompt_tokens, events[-1].usage.completion_tokens) == (28, 2)
