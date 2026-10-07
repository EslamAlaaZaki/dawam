"""The ``gemini`` adapter: Google Gemini (``generateContent``) on the Gemini API or Vertex AI.

The base URL is everything before ``/models/{model}``: ``https://generativelanguage.googleapis.com/v1beta``
for the Gemini API, or the publisher path of a Vertex AI region,
``https://us-central1-aiplatform.googleapis.com/v1/projects/P/locations/L/publishers/google``.
Embeddings use ``batchEmbedContents`` (the Gemini API).

Authentication is the ``x-goog-api-key`` header, or a service account when the credential is
the service account's JSON key: the adapter signs a JWT with its private key, exchanges it
for an access token through the same transport, caches the token until shortly before it
expires and asks for a fresh one after a 401. Neither the key, the secret nor the token is
ever logged or put into an error message.
"""

from __future__ import annotations

import base64
import io
import json
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..gateway import (
    AUTH,
    BAD_REQUEST,
    INVALID_RESPONSE,
    UNAVAILABLE,
    ChatEvent,
    Done,
    LlmError,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    Usage,
)
from .transport import HttpResponse, Transport, TransportError
from .wire import detail, error_for, json_body

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
REFRESH_MARGIN_SECONDS = 300
JWT_LIFETIME_SECONDS = 3600

_FINISH_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}
_FILTERED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ServiceAccountTokenSource:
    """OAuth2 access tokens for a Google service account (JWT bearer grant), cached."""

    def __init__(
        self,
        key: dict[str, Any],
        *,
        transport: Transport,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._email = key["client_email"]
        self._private_key = key["private_key"]
        self._token_uri = key.get("token_uri") or GOOGLE_TOKEN_URI
        self._transport = transport
        self._timeout = timeout_seconds
        self._clock = clock
        self._wall_clock = wall_clock
        self._token: str | None = None
        self._expires_at = 0.0

    def __call__(self) -> str:
        if self._token is None or self._clock() >= self._expires_at:
            self._fetch()
        assert self._token is not None
        return self._token

    def invalidate(self) -> None:
        self._token = None

    def redact(self, text: str) -> str:
        """``text`` without the private key or the current token (a provider may echo them)."""
        for secret in (self._private_key, self._token):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def _assertion(self) -> str:
        issued = int(self._wall_clock())
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self._email,
            "scope": GOOGLE_SCOPE,
            "aud": self._token_uri,
            "iat": issued,
            "exp": issued + JWT_LIFETIME_SECONDS,
        }
        signing_input = ".".join(
            _b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
        )
        try:
            private = serialization.load_pem_private_key(self._private_key.encode(), password=None)
            if not isinstance(private, rsa.RSAPrivateKey):
                raise ValueError
            signature = private.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        except (ValueError, TypeError):
            raise LlmError(AUTH, "The service account's private key is not valid.") from None
        return f"{signing_input}.{_b64url(signature)}"

    def _fetch(self) -> None:
        form = urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(),
            }
        ).encode("ascii")
        try:
            response = self._transport.request(
                "POST",
                self._token_uri,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body=form,
                timeout=self._timeout,
            )
        except TransportError:
            raise LlmError(UNAVAILABLE, "Google sign-in could not be reached.") from None
        try:
            raw = response.read()
        except OSError:
            raise LlmError(UNAVAILABLE, "Google sign-in could not be reached.") from None
        finally:
            response.close()
        if response.status >= 500:
            raise LlmError(UNAVAILABLE, "Google sign-in is unavailable.")
        try:
            data = json.loads(raw)
            token, lifetime = data["access_token"], float(data["expires_in"])
            if response.status != 200 or not isinstance(token, str) or not token:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise LlmError(AUTH, "Google rejected the service account.") from None
        self._token = token
        self._expires_at = self._clock() + max(lifetime - REFRESH_MARGIN_SECONDS, 0)


def _service_account(
    credential: str, transport: Transport, timeout: float, clock: Callable[[], float]
) -> Callable[[], str]:
    try:
        key = json.loads(credential)
        if not (
            isinstance(key, dict)
            and isinstance(key.get("client_email"), str)
            and isinstance(key.get("private_key"), str)
        ):
            raise ValueError
        token_uri = key.get("token_uri")
        if token_uri is not None and urlsplit(str(token_uri)).scheme != "https":
            raise ValueError
    except ValueError:
        return _malformed_credential
    return ServiceAccountTokenSource(key, transport=transport, timeout_seconds=timeout, clock=clock)


def _malformed_credential() -> str:
    raise LlmError(AUTH, "The credential must be an API key or a service account JSON key.")


class GeminiAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        transport: Transport,
        token_provider: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport
        if api_key and api_key.lstrip().startswith("{") and token_provider is None:
            token_provider = _service_account(api_key, transport, timeout_seconds, clock)
            api_key = None
        self._api_key = api_key
        self._token_provider = token_provider

    # -- Adapter ---------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]:
        system, contents = _wire_contents(messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parametersJsonSchema": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        if json_schema is not None:
            body["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseJsonSchema": json_schema,
            }
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        response = self._send("POST", f"{_model_path(model)}:{method}", json.dumps(body).encode())
        if response.status >= 400:
            raise self._error(response)
        try:
            if stream:
                yield from _stream_events(response)
            else:
                yield from _reply_events(json_body(response))
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        name = _model_path(model)
        body = {
            "requests": [
                {"model": name.lstrip("/"), "content": {"parts": [{"text": text}]}}
                for text in texts
            ]
        }
        response = self._send("POST", f"{name}:batchEmbedContents", json.dumps(body).encode())
        try:
            if response.status >= 400:
                raise self._error(response)
            data = json_body(response)
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()
        try:
            vectors = [[float(x) for x in item["values"]] for item in data["embeddings"]]
        except (KeyError, TypeError, ValueError):
            raise LlmError(
                INVALID_RESPONSE, "The provider's embeddings reply was malformed."
            ) from None
        if len(vectors) != len(texts):
            raise LlmError(INVALID_RESPONSE, "The provider returned a different number of vectors.")
        return vectors

    def context_window(self, model: str) -> int | None:
        try:
            response = self._send("GET", _model_path(model), None)
        except LlmError:
            return None
        try:
            if response.status != 200:
                return None
            data = json_body(response)
        except (LlmError, OSError):
            return None
        finally:
            response.close()
        value = data.get("inputTokenLimit") if isinstance(data, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    # -- HTTP ------------------------------------------------------------------------

    def _send(self, method: str, path: str, body: bytes | None) -> HttpResponse:
        response = self._send_once(method, path, body)
        source = self._token_provider
        if response.status == 401 and isinstance(source, ServiceAccountTokenSource):
            response.close()
            source.invalidate()  # the token may have been revoked: one retry with a fresh one
            response = self._send_once(method, path, body)
        return response

    def _send_once(self, method: str, path: str, body: bytes | None) -> HttpResponse:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        source = self._token_provider
        try:
            if source is not None:
                headers["Authorization"] = f"Bearer {source()}"
            elif self._api_key:
                headers["x-goog-api-key"] = self._api_key
            return self._transport.request(
                method, self._base_url + path, headers=headers, body=body, timeout=self._timeout
            )
        except TransportError as exc:
            raise LlmError(UNAVAILABLE, str(exc)) from None

    def _error(self, response: HttpResponse) -> LlmError:
        """Gemini reports a wrong API key as a 400, so that one is told apart by its reason."""
        try:
            raw = response.read()
        except OSError:
            raw = b""
        finally:
            response.close()
        buffered = HttpResponse(response.status, response.headers, io.BytesIO(raw))
        error = error_for(buffered)
        source = self._token_provider
        if isinstance(source, ServiceAccountTokenSource):
            return LlmError(error.code, source.redact(error.message), retry_after=error.retry_after)
        lowered = raw.lower()
        if response.status == 400 and (
            b"api_key_invalid" in lowered or b"api key not valid" in lowered
        ):
            return LlmError(AUTH, "The provider rejected the API key.")
        return error


def _model_path(model: str) -> str:
    name = model[len("models/") :] if model.startswith("models/") else model
    return f"/models/{quote(name, safe='')}"


def _wire_contents(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
    """System messages become ``systemInstruction``; a tool result is a ``user`` part that
    names the call it answers, and consecutive ``user`` turns merge (parallel calls answer
    in one turn)."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    names: dict[str, str] = {}

    def add(role: str, parts: list[dict[str, Any]]) -> None:
        if contents and contents[-1]["role"] == role == "user":
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "user":
            add("user", [{"text": message.content or ""}])
        elif message.role == "tool":
            call_id = message.tool_call_id or ""
            response = {
                "id": call_id,
                "name": names.get(call_id, ""),
                "response": {"result": message.content or ""},
            }
            add("user", [{"functionResponse": response}])
        else:
            parts: list[dict[str, Any]] = []
            if message.content:
                parts.append({"text": message.content})
            for call in message.tool_calls:
                names[call.id] = call.name
                parts.append(
                    {"functionCall": {"id": call.id, "name": call.name, "args": call.arguments}}
                )
            add("model", parts or [{"text": ""}])
    return "\n\n".join(system), contents


def _usage(raw: Any, previous: Usage | None = None) -> Usage | None:
    """Gemini repeats cumulative usage in each stream chunk; the last complete one wins."""
    if not isinstance(raw, dict):
        return previous
    prompt = raw.get("promptTokenCount")
    if not isinstance(prompt, int) or isinstance(prompt, bool):
        return previous
    completion = 0
    for key in ("candidatesTokenCount", "thoughtsTokenCount"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            completion += value
    if "candidatesTokenCount" not in raw and previous is not None:
        return previous  # an early chunk that counts the prompt only
    return Usage(prompt, completion)


def _finish(reason: Any, called_tools: bool) -> str | None:
    if called_tools:
        return "tool_calls"
    if not isinstance(reason, str):
        return None
    if reason in _FILTERED:
        return "content_filter"
    return _FINISH_REASONS.get(reason, reason.lower())


def _tool_call(call: Any) -> ToolCall:
    try:
        arguments = call.get("args") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments are not an object")
        call_id = call.get("id") or f"call_{uuid.uuid4().hex[:16]}"
        return ToolCall(id=str(call_id), name=str(call["name"]), arguments=arguments)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LlmError(INVALID_RESPONSE, "The model made a tool call that is not valid.") from None


def _candidate_parts(data: Any) -> tuple[list[Any], Any]:
    """The parts and finish reason of a reply (or chunk); a blocked prompt is a bad request."""
    if not isinstance(data, dict):
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
    candidates = data.get("candidates")
    if not candidates:
        feedback = data.get("promptFeedback")
        reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
        if reason:
            raise LlmError(BAD_REQUEST, f"The provider blocked the prompt ({reason}).")
        if "usageMetadata" in data:
            return [], None
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
    candidate = candidates[0]
    content = candidate.get("content") if isinstance(candidate, dict) else None
    parts = content.get("parts", []) if isinstance(content, dict) else []
    if not isinstance(parts, list):
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
    return parts, candidate.get("finishReason")


def _part_events(parts: list[Any]) -> Iterator[ChatEvent]:
    for part in parts:
        if not isinstance(part, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
        if isinstance(part.get("functionCall"), dict):
            yield ToolCallEvent(_tool_call(part["functionCall"]))
        elif part.get("text") and not part.get("thought"):
            yield TextDelta(str(part["text"]))


def _reply_events(data: Any) -> Iterator[ChatEvent]:
    parts, reason = _candidate_parts(data)
    called = False
    for event in _part_events(parts):
        called = called or isinstance(event, ToolCallEvent)
        yield event
    yield Done(_usage(data.get("usageMetadata")), _finish(reason, called))


def _stream_events(response: HttpResponse) -> Iterator[ChatEvent]:
    usage: Usage | None = None
    reason: Any = None
    called = False
    for line in response.iter_lines():
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("data:"):
            continue
        try:
            chunk = json.loads(text[len("data:") :].strip())
        except ValueError:
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.") from None
        if isinstance(chunk, dict) and "error" in chunk:
            raise LlmError(UNAVAILABLE, "The provider failed mid-stream: " + detail(chunk))
        parts, finish = _candidate_parts(chunk)
        for event in _part_events(parts):
            called = called or isinstance(event, ToolCallEvent)
            yield event
        reason = finish or reason
        usage = _usage(chunk.get("usageMetadata"), usage)
    if reason is None and not called:
        raise LlmError(UNAVAILABLE, "The provider's stream ended unexpectedly.")
    yield Done(usage, _finish(reason, called))
