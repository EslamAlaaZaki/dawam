"""The ``azure_openai`` adapter: Azure OpenAI deployments (``/openai/deployments/{name}/...``).

The provider's base URL is the resource endpoint (``https://myres.openai.azure.com``),
optionally with ``?api-version=...`` (otherwise ``DEFAULT_API_VERSION``). The model name
of a ``Model`` is the deployment name. The wire format is the OpenAI one, so the request
and reply handling is inherited. Authentication is the ``api-key`` header, or, when the
credential is written ``entra:<access token>``, an Entra ID bearer token.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .openai_compatible import OpenAICompatibleAdapter
from .transport import HttpResponse, Transport

DEFAULT_API_VERSION = "2024-10-21"
ENTRA_PREFIX = "entra:"


class AzureOpenAIAdapter(OpenAICompatibleAdapter):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        transport: Transport,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        query = dict(parse_qsl(parts.query))
        self._api_version = query.pop("api-version", DEFAULT_API_VERSION)
        endpoint = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        if api_key and api_key.startswith(ENTRA_PREFIX) and token_provider is None:
            token = api_key[len(ENTRA_PREFIX) :]
            token_provider, api_key = (lambda: token), None
        self._token_provider = token_provider
        super().__init__(
            base_url=endpoint,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def _post(self, path: str, body: dict[str, Any]) -> HttpResponse:
        deployment = quote(str(body["model"]), safe="")
        return super()._post(f"/openai/deployments/{deployment}{path}", body)

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
