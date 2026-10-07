"""The ``bedrock`` adapter: models on AWS Bedrock through the Converse API.

The base URL is the regional runtime endpoint (``https://bedrock-runtime.us-east-1.amazonaws.com``);
the region is read from it, and the ``Model`` name is the Bedrock model ID (or inference
profile ID or ARN). Chat with tools, streaming and structured output; Bedrock models have
no common embeddings call, so ``embed`` refuses, and the runtime API reports no context
window, so the admin enters it.

Requests are signed with AWS Signature Version 4 by this module (no AWS SDK). The credential
is ``<access key id>:<secret access key>[:<session token>]``, optionally followed by
``;role=<role ARN>``, in which case those credentials call STS ``AssumeRole`` and the role's
temporary credentials sign the Bedrock calls (cached until shortly before they expire, and
renewed after a 403). The secret and tokens are never logged or put into an error message.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
import time
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from ..gateway import (
    AUTH,
    BAD_REQUEST,
    INVALID_RESPONSE,
    RATE_LIMIT,
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

DEFAULT_MAX_TOKENS = 4096
"""A cap on the reply that fits the Bedrock models' output limits."""
ROLE_SUFFIX = ";role="
ROLE_SESSION_NAME = "dawam"
ROLE_LIFETIME_SECONDS = 3600
REFRESH_MARGIN_SECONDS = 300
_REGION = re.compile(r"bedrock-runtime(?:-fips)?\.([a-z0-9-]+)\.")

_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "content_filtered": "content_filter",
    "guardrail_intervened": "content_filter",
}


# -- SigV4 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AwsCredentials:
    access_key: str = ""
    secret_key: str = ""
    session_token: str | None = None

    def __repr__(self) -> str:  # a secret must not reach a log line
        return "AwsCredentials(...)"


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def sign_v4(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    credentials: AwsCredentials,
    region: str,
    service: str,
    now: datetime,
) -> dict[str, str]:
    """``headers`` plus ``X-Amz-Date`` (and the session token) and the ``Authorization``
    header that signs the request (AWS Signature Version 4)."""
    parts = urlsplit(url)
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    signed = {key: value for key, value in headers.items()}
    signed["X-Amz-Date"] = stamp
    if credentials.session_token:
        signed["X-Amz-Security-Token"] = credentials.session_token
    to_sign = {"host": parts.netloc, **{k.lower(): " ".join(v.split()) for k, v in signed.items()}}
    names = sorted(to_sign)
    # The path is encoded once as sent and once more for the signature (every service but S3).
    canonical_path = quote(parts.path or "/", safe="/-_.~")
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted(parse_qsl(parts.query, keep_blank_values=True))
    )
    canonical = "\n".join(
        [
            method,
            canonical_path,
            canonical_query,
            "".join(f"{name}:{to_sign[name]}\n" for name in names),
            ";".join(names),
            hashlib.sha256(body).hexdigest(),
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    key = _hmac(("AWS4" + credentials.secret_key).encode(), day)
    for step in (region, service, "aws4_request"):
        key = _hmac(key, step)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    signed["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={credentials.access_key}/{scope}, "
        f"SignedHeaders={';'.join(names)}, Signature={signature}"
    )
    return signed


# -- Credentials ---------------------------------------------------------------------


class AssumedRole:
    """Temporary credentials of an IAM role, from STS ``AssumeRole``, cached until near expiry."""

    def __init__(
        self,
        role_arn: str,
        base: AwsCredentials,
        *,
        region: str,
        transport: Transport,
        timeout_seconds: float,
        clock: Callable[[], float],
        now: Callable[[], datetime],
    ) -> None:
        self._arn = role_arn
        self._base = base
        self._region = region
        self._transport = transport
        self._timeout = timeout_seconds
        self._clock = clock
        self._now = now
        self._credentials: AwsCredentials | None = None
        self._expires_at = 0.0

    def __call__(self) -> AwsCredentials:
        if self._credentials is None or self._clock() >= self._expires_at:
            self._fetch()
        assert self._credentials is not None
        return self._credentials

    def invalidate(self) -> None:
        self._credentials = None

    def _fetch(self) -> None:
        form = urlencode(
            {
                "Action": "AssumeRole",
                "Version": "2011-06-15",
                "RoleArn": self._arn,
                "RoleSessionName": ROLE_SESSION_NAME,
                "DurationSeconds": ROLE_LIFETIME_SECONDS,
            }
        ).encode("ascii")
        url = f"https://sts.{self._region}.amazonaws.com/"
        headers = sign_v4(
            "POST",
            url,
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            form,
            credentials=self._base,
            region=self._region,
            service="sts",
            now=self._now(),
        )
        try:
            response = self._transport.request(
                "POST", url, headers=headers, body=form, timeout=self._timeout
            )
        except TransportError:
            raise LlmError(UNAVAILABLE, "AWS STS could not be reached.") from None
        try:
            raw = response.read()
        except OSError:
            raise LlmError(UNAVAILABLE, "AWS STS could not be reached.") from None
        finally:
            response.close()
        if response.status >= 500:
            raise LlmError(UNAVAILABLE, "AWS STS is unavailable.")
        try:
            found = json.loads(raw)["AssumeRoleResponse"]["AssumeRoleResult"]["Credentials"]
            credentials = AwsCredentials(
                found["AccessKeyId"], found["SecretAccessKey"], found["SessionToken"]
            )
            if response.status != 200 or not all(
                isinstance(v, str) and v
                for v in (credentials.access_key, credentials.secret_key, credentials.session_token)
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise LlmError(AUTH, "AWS rejected the request to assume the role.") from None
        self._credentials = credentials
        self._expires_at = self._clock() + ROLE_LIFETIME_SECONDS - REFRESH_MARGIN_SECONDS


def _parse_credential(credential: str | None) -> tuple[AwsCredentials, str | None]:
    """``(credentials, role ARN or None)`` from ``AKID:SECRET[:TOKEN][;role=ARN]``."""
    text, found, role = (credential or "").strip().partition(ROLE_SUFFIX)
    role = role.strip()
    fields = [field.strip() for field in text.split(":", 2)]
    if len(fields) < 2 or not fields[0] or not fields[1] or (found and not role):
        raise LlmError(
            AUTH, "The credential must be <access key id>:<secret access key>[:<session token>]."
        )
    token = fields[2] if len(fields) == 3 and fields[2] else None
    return AwsCredentials(fields[0], fields[1], token), (role or None)


# -- Adapter -------------------------------------------------------------------------


class BedrockAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        transport: Transport,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport
        self._now = now
        match = _REGION.search(urlsplit(self._base_url).netloc)
        self._region = match.group(1) if match else None
        self._credential = api_key
        self._clock = clock
        self._role: AssumedRole | None = None
        self._static: AwsCredentials | None = None

    # -- Adapter ---------------------------------------------------------------------

    def chat(
        self,
        model: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        stream: bool,
        json_schema: dict[str, Any] | None,
    ) -> Iterator[ChatEvent]:
        system, wire_messages = _wire_messages(messages)
        body: dict[str, Any] = {
            "messages": wire_messages,
            "inferenceConfig": {"maxTokens": DEFAULT_MAX_TOKENS},
        }
        if system:
            body["system"] = [{"text": text} for text in system]
        if tools:
            body["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": {"json": t.parameters},
                        }
                    }
                    for t in tools
                ]
            }
        if json_schema is not None:
            body["outputConfig"] = {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {"name": "result", "schema": json.dumps(json_schema)}
                    },
                }
            }
        operation = "converse-stream" if stream else "converse"
        path = f"/model/{quote(model, safe='')}/{operation}"
        response = self._send("POST", path, json.dumps(body).encode("utf-8"))
        if response.status >= 400:
            try:
                raise error_for(response, self._redact)
            finally:
                response.close()
        try:
            if stream:
                yield from _stream_events(response, self._redact)
            else:
                yield from _reply_events(json_body(response))
        except OSError:
            raise LlmError(UNAVAILABLE, "The connection to the provider was lost.") from None
        finally:
            response.close()

    def embed(self, model: str, texts: Sequence[str]) -> list[list[float]]:
        raise LlmError(BAD_REQUEST, "Bedrock has no common embeddings call: use another provider.")

    def context_window(self, model: str) -> int | None:
        return None

    # -- HTTP ------------------------------------------------------------------------

    def _redact(self, text: str) -> str:
        """``text`` without the secret key or session tokens (a provider may echo them)."""
        held = (self._static, self._role._credentials if self._role else None)
        for credentials in held:
            if credentials is not None:
                for secret in (credentials.secret_key, credentials.session_token):
                    if secret:
                        text = text.replace(secret, "[redacted]")
        return text

    def _credentials(self) -> AwsCredentials:
        if self._static is None:
            self._static, role = _parse_credential(self._credential)
            if role:
                self._role = AssumedRole(
                    role,
                    self._static,
                    region=self._region or "",
                    transport=self._transport,
                    timeout_seconds=self._timeout,
                    clock=self._clock,
                    now=self._now,
                )
        return self._role() if self._role is not None else self._static

    def _send(self, method: str, path: str, body: bytes) -> HttpResponse:
        if self._region is None:
            raise LlmError(
                BAD_REQUEST,
                "The base URL must be a bedrock-runtime.<region>.amazonaws.com endpoint.",
            )
        response = self._send_once(method, path, body)
        if response.status == 403 and self._role is not None:
            response.close()
            self._role.invalidate()  # the role's credentials may have expired: retry once
            response = self._send_once(method, path, body)
        return response

    def _send_once(self, method: str, path: str, body: bytes) -> HttpResponse:
        assert self._region is not None
        url = self._base_url + path
        try:
            headers = sign_v4(
                method,
                url,
                {"Accept": "application/json", "Content-Type": "application/json"},
                body,
                credentials=self._credentials(),
                region=self._region,
                service="bedrock",
                now=self._now(),
            )
            return self._transport.request(
                method, url, headers=headers, body=body, timeout=self._timeout
            )
        except TransportError as exc:
            raise LlmError(UNAVAILABLE, str(exc)) from None


def _wire_messages(messages: Sequence[Message]) -> tuple[list[str], list[dict[str, Any]]]:
    """System messages become the top-level ``system``; a tool result is a ``user`` block,
    and consecutive ``user`` turns merge (Converse wants alternating roles)."""
    system: list[str] = []
    wire: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if wire and wire[-1]["role"] == role == "user":
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": blocks})

    for message in messages:
        if message.role == "system":
            if message.content:
                system.append(message.content)
        elif message.role == "user":
            add("user", [{"text": message.content or ""}])
        elif message.role == "tool":
            result = {
                "toolUseId": message.tool_call_id,
                "content": [{"text": message.content or ""}],
            }
            add("user", [{"toolResult": result}])
        else:
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"text": message.content})
            blocks.extend(
                {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}}
                for c in message.tool_calls
            )
            add("assistant", blocks or [{"text": ""}])
    return system, wire


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage(raw: Any) -> Usage | None:
    """Prompt tokens include cached input, which Bedrock reports separately."""
    if not isinstance(raw, dict) or not isinstance(raw.get("inputTokens"), int):
        return None
    prompt = (
        _count(raw.get("inputTokens"))
        + _count(raw.get("cacheReadInputTokens"))
        + _count(raw.get("cacheWriteInputTokens"))
    )
    return Usage(prompt, _count(raw.get("outputTokens")))


def _finish(stop_reason: Any) -> str | None:
    return _FINISH_REASONS.get(stop_reason, stop_reason) if isinstance(stop_reason, str) else None


def _tool_call(block: Any, arguments: Any) -> ToolCall:
    try:
        if not isinstance(arguments, dict):
            raise ValueError("arguments are not an object")
        return ToolCall(
            id=str(block.get("toolUseId") or ""), name=str(block["name"]), arguments=arguments
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LlmError(INVALID_RESPONSE, "The model made a tool call that is not valid.") from None


def _reply_events(data: Any) -> Iterator[ChatEvent]:
    message = data.get("output", {}).get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
    for block in content:
        if not isinstance(block, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's chat reply was malformed.")
        if block.get("text"):
            yield TextDelta(str(block["text"]))
        elif isinstance(block.get("toolUse"), dict):
            use = block["toolUse"]
            yield ToolCallEvent(_tool_call(use, use.get("input")))
    yield Done(_usage(data.get("usage")), _finish(data.get("stopReason")))


# -- Streaming (AWS event stream) ------------------------------------------------------

_FIXED_HEADER_SIZES = {0: 0, 1: 0, 2: 1, 3: 2, 4: 4, 5: 8, 8: 8, 9: 16}


def _read_exact(response: HttpResponse, size: int) -> bytes:
    chunks: list[bytes] = []
    while size > 0:
        chunk = response.read(size)
        if not chunk:
            break
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _headers(raw: bytes) -> dict[str, str]:
    found: dict[str, str] = {}
    at = 0
    while at < len(raw):
        name_end = at + 1 + raw[at]
        name = raw[at + 1 : name_end].decode("utf-8", "replace")
        kind = raw[name_end]
        at = name_end + 1
        if kind in (6, 7):
            (length,) = struct.unpack_from(">H", raw, at)
            value = raw[at + 2 : at + 2 + length]
            at += 2 + length
            if kind == 7:
                found[name] = value.decode("utf-8", "replace")
        elif kind in _FIXED_HEADER_SIZES:
            at += _FIXED_HEADER_SIZES[kind]
        else:
            raise ValueError("unknown header type")
    return found


def _frames(response: HttpResponse) -> Iterator[tuple[dict[str, str], bytes]]:
    """The ``(headers, payload)`` of each frame of an AWS event stream, checksums verified."""
    while True:
        prelude = _read_exact(response, 12)
        if not prelude:
            return
        try:
            if len(prelude) < 12:
                raise ValueError("short prelude")
            total, header_length, prelude_crc = struct.unpack(">III", prelude)
            if zlib.crc32(prelude[:8]) != prelude_crc or total < 16 + header_length:
                raise ValueError("bad prelude")
            rest = _read_exact(response, total - 12)
            if len(rest) < total - 12:
                raise ValueError("short frame")
            (message_crc,) = struct.unpack(">I", rest[-4:])
            if zlib.crc32(rest[:-4], zlib.crc32(prelude)) != message_crc:
                raise ValueError("bad checksum")
            yield _headers(rest[:header_length]), rest[header_length:-4]
        except (ValueError, struct.error, IndexError):
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.") from None


def _stream_error(kind: str, payload: Any, redact: Callable[[str], str]) -> LlmError:
    message = detail(payload, redact)
    suffix = f" ({message})" if message else ""
    lowered = kind.lower()
    if "throttl" in lowered:
        return LlmError(RATE_LIMIT, "The provider is rate limiting requests." + suffix)
    if "validation" in lowered:
        return LlmError(BAD_REQUEST, "The provider refused the request." + suffix)
    if "accessdenied" in lowered:
        return LlmError(AUTH, "The provider rejected the credentials." + suffix)
    return LlmError(UNAVAILABLE, "The provider failed mid-stream." + suffix)


def _stream_events(response: HttpResponse, redact: Callable[[str], str]) -> Iterator[ChatEvent]:
    tools: dict[int, dict[str, Any]] = {}
    usage: Usage | None = None
    finish: str | None = None
    stopped = False
    for headers, raw in _frames(response):
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.") from None
        if headers.get(":message-type") == "exception":
            raise _stream_error(headers.get(":exception-type", ""), payload, redact)
        if not isinstance(payload, dict):
            raise LlmError(INVALID_RESPONSE, "The provider's stream was malformed.")
        kind = headers.get(":event-type")
        index = payload.get("contentBlockIndex")
        index = index if isinstance(index, int) else 0
        if kind == "contentBlockStart":
            start = payload.get("start")
            use = start.get("toolUse") if isinstance(start, dict) else None
            if isinstance(use, dict):
                tools[index] = {"block": use, "json": ""}
        elif kind == "contentBlockDelta":
            delta = payload.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            if delta.get("text"):
                yield TextDelta(str(delta["text"]))
            elif isinstance(delta.get("toolUse"), dict) and index in tools:
                tools[index]["json"] += str(delta["toolUse"].get("input") or "")
        elif kind == "contentBlockStop":
            slot = tools.pop(index, None)
            if slot is not None:
                yield ToolCallEvent(_tool_call(slot["block"], _parse_arguments(slot["json"])))
        elif kind == "messageStop":
            finish = _finish(payload.get("stopReason"))
            stopped = True
        elif kind == "metadata":
            usage = _usage(payload.get("usage")) or usage
    if not stopped:
        raise LlmError(UNAVAILABLE, "The provider's stream ended unexpectedly.")
    yield Done(usage, finish)


def _parse_arguments(raw: str) -> Any:
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
