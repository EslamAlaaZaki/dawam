"""The ``azure_openai`` adapter: Azure OpenAI deployments (``/openai/deployments/{name}/...``).

The provider's base URL is the resource endpoint (``https://myres.openai.azure.com``),
optionally with ``?api-version=...`` (otherwise ``DEFAULT_API_VERSION``). The model name
of a ``Model`` is the deployment name. The wire format is the OpenAI one, so the request
and reply handling is inherited.

Authentication is the ``api-key`` header, or Entra ID when the credential is written
``entra:<tenant_id>:<client_id>:<client_secret>``: the adapter runs the client-credentials
flow through the same transport, caches the token until shortly before it expires and
asks for a fresh one after that (and once more after a 401). Neither the secret nor the
token is ever logged or put into an error message.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ..gateway import AUTH, UNAVAILABLE, LlmError
from .openai_compatible import OpenAICompatibleAdapter
from .transport import HttpResponse, Transport, TransportError

DEFAULT_API_VERSION = "2024-10-21"
ENTRA_PREFIX = "entra:"
ENTRA_LOGIN = "https://login.microsoftonline.com"
ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"
REFRESH_MARGIN_SECONDS = 300


class EntraTokenSource:
    """Client-credentials tokens for Azure Cognitive Services, cached until near expiry."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        transport: Transport,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenant = tenant_id
        self._client_id = client_id
        self._secret = client_secret
        self._transport = transport
        self._timeout = timeout_seconds
        self._clock = clock
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
        """``text`` without the client secret or the current token (a provider may echo them)."""
        for secret in (self._secret, self._token):
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def _fetch(self) -> None:
        form = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._secret,
                "scope": ENTRA_SCOPE,
            }
        ).encode("ascii")
        url = f"{ENTRA_LOGIN}/{quote(self._tenant, safe='')}/oauth2/v2.0/token"
        try:
            response = self._transport.request(
                "POST",
                url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body=form,
                timeout=self._timeout,
            )
        except TransportError:
            raise LlmError(UNAVAILABLE, "Microsoft Entra ID could not be reached.") from None
        try:
            raw = response.read()
        except OSError:
            raise LlmError(UNAVAILABLE, "Microsoft Entra ID could not be reached.") from None
        finally:
            response.close()
        if response.status >= 500:
            raise LlmError(UNAVAILABLE, "Microsoft Entra ID is unavailable.")
        try:
            data = json.loads(raw)
            token, lifetime = data["access_token"], float(data["expires_in"])
            if response.status != 200 or not isinstance(token, str) or not token:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise LlmError(AUTH, "Microsoft Entra ID rejected the client credentials.") from None
        self._token = token
        self._expires_at = self._clock() + max(lifetime - REFRESH_MARGIN_SECONDS, 0)


def _redacted(error: LlmError, source: EntraTokenSource) -> LlmError:
    return LlmError(error.code, source.redact(error.message), retry_after=error.retry_after)


def _malformed_credential() -> str:
    raise LlmError(AUTH, "The Entra ID credential must be entra:<tenant>:<client id>:<secret>.")


class AzureOpenAIAdapter(OpenAICompatibleAdapter):
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
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query))
        self._api_version = query.pop("api-version", DEFAULT_API_VERSION)
        endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        if api_key and api_key.startswith(ENTRA_PREFIX) and token_provider is None:
            fields = api_key[len(ENTRA_PREFIX) :].split(":", 2)
            if len(fields) != 3 or not all(fields):
                token_provider = _malformed_credential
            else:
                token_provider = EntraTokenSource(
                    *fields, transport=transport, timeout_seconds=timeout_seconds, clock=clock
                )
            api_key = None
        self._token_provider = token_provider
        super().__init__(
            base_url=endpoint,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def _post(self, path: str, body: dict[str, Any]) -> HttpResponse:
        deployment = quote(str(body["model"]), safe="")
        full = f"/openai/deployments/{deployment}{path}"
        source = self._token_provider
        if isinstance(source, EntraTokenSource):
            source()  # a failure to get a token is final: only a rejected request is retried
        try:
            return super()._post(full, body)
        except LlmError as exc:
            if not isinstance(source, EntraTokenSource):
                raise
            if exc.code != AUTH:
                raise _redacted(exc, source) from None
            source.invalidate()  # the token may have been revoked: one retry with a fresh one
            try:
                return super()._post(full, body)
            except LlmError as again:
                raise _redacted(again, source) from None

    def _url(self, path: str) -> str:
        if path == "/models":
            path = "/openai/models"
        return f"{self._base_url}{path}?api-version={quote(self._api_version, safe='')}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._token_provider is not None:
            headers["Authorization"] = f"Bearer {self._token_provider()}"
        elif self._api_key:
            headers["api-key"] = self._api_key
        return headers
