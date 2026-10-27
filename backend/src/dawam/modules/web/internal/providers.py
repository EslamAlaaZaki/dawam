"""The web search port and its providers (ADR 0008).

``SearchProvider`` is the port: ``search(query, limit) -> results``. Two adapters exist,
both free: **SearXNG** (self-hosted, internal: the admin's URL, no key) and **LangSearch**
(a cloud API, external: an API key). Another provider is one more adapter here and one
more entry in ``KINDS``. Each adapter speaks HTTP through the ``Network`` port only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from .network import HttpRequest, Network, NetworkError

SEARCH_TIMEOUT_SECONDS = 15.0
MAX_SEARCH_BYTES = 1_000_000
MAX_SNIPPET_CHARS = 600
LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"


@dataclass(frozen=True)
class ProviderKind:
    kind: str
    label: str
    internal: bool
    """Runs inside the installation's network: the only kind an internal-only Workspace uses."""


KINDS: dict[str, ProviderKind] = {
    "searxng": ProviderKind("searxng", "SearXNG", internal=True),
    "langsearch": ProviderKind("langsearch", "LangSearch", internal=False),
}


class SearchError(Exception):
    """The provider could not answer; the message is safe to show (never a key)."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    def search(self, query: str, *, limit: int) -> list[SearchResult]: ...


def _json(network: Network, request: HttpRequest, label: str, denied: str) -> Any:
    """The provider's JSON answer; ``denied`` is the error for a 401 or 403."""
    try:
        got = network.send(request)
    except NetworkError as exc:
        raise SearchError(f"{label} could not be reached: {exc}") from None
    if got.status in (401, 403):
        raise SearchError(denied)
    if got.status >= 400:
        raise SearchError(f"{label} answered with HTTP {got.status}.")
    try:
        return json.loads(got.body)
    except ValueError:
        raise SearchError(f"{label} did not answer with JSON.") from None


def _result(title: Any, url: Any, snippet: Any) -> SearchResult | None:
    if not isinstance(url, str) or urlsplit(url).scheme not in ("http", "https"):
        return None
    return SearchResult(
        str(title or url).strip(), url, " ".join(str(snippet or "").split())[:MAX_SNIPPET_CHARS]
    )


class Searxng:
    """``GET <url>/search?q=...&format=json``; the server's ``settings.yml`` must list
    ``json`` under ``search.formats``."""

    def __init__(self, base_url: str, network: Network) -> None:
        self._base_url = base_url.rstrip("/")
        self._network = network

    def search(self, query: str, *, limit: int) -> list[SearchResult]:
        data = _json(
            self._network,
            HttpRequest(
                "GET",
                f"{self._base_url}/search?{urlencode({'q': query, 'format': 'json'})}",
                headers={"Accept": "application/json"},
                timeout=SEARCH_TIMEOUT_SECONDS,
                max_bytes=MAX_SEARCH_BYTES,
            ),
            "SearXNG",
            "SearXNG refused the JSON format: add `json` to `search.formats` in its settings.yml.",
        )
        if not isinstance(data, dict):
            raise SearchError("SearXNG did not answer with search results.")
        found = [
            _result(item.get("title"), item.get("url"), item.get("content"))
            for item in data.get("results") or []
            if isinstance(item, dict)
        ]
        return [r for r in found if r is not None][:limit]


class LangSearch:
    """``POST https://api.langsearch.com/v1/web-search`` with a Bearer key."""

    def __init__(self, api_key: str, network: Network, url: str = LANGSEARCH_URL) -> None:
        self._api_key = api_key
        self._network = network
        self._url = url

    def search(self, query: str, *, limit: int) -> list[SearchResult]:
        body = {"query": query, "count": limit, "summary": True, "freshness": "noLimit"}
        data = _json(
            self._network,
            HttpRequest(
                "POST",
                self._url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body=json.dumps(body).encode(),
                timeout=SEARCH_TIMEOUT_SECONDS,
                max_bytes=MAX_SEARCH_BYTES,
            ),
            "LangSearch",
            "LangSearch rejected the API key.",
        )
        if not isinstance(data, dict):
            raise SearchError("LangSearch did not answer with search results.")
        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        found = [
            _result(item.get("name"), item.get("url"), item.get("summary") or item.get("snippet"))
            for item in pages
            if isinstance(item, dict)
        ]
        return [r for r in found if r is not None][:limit]
