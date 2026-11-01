"""Web module: outbound web access for the assistant, web search and web fetch (ADR 0008).

Public interface. Other modules import only what is re-exported here:

- ``WebService``: the admin's settings (``WebSettings``: one on/off switch, off by default;
  the registered search providers, ``WebProvider``; the provider of normal Workspaces and
  the one of internal-only Workspaces, internal providers only) and, for the assistant's
  tools, ``search_provider(workspace_id)`` / ``fetch_available(workspace_id)`` (``None`` /
  ``False``: the tool is not offered), ``search`` (values stripped from the query with
  ``strip_values``, audited, returns ``WebSearch``) and ``fetch`` (audited; private and
  internal addresses refused after DNS and on every redirect, size, time and type capped,
  returns ``FetchedPage``). The Workspace's internal-only flag reaches it as the
  ``internal_only`` port, which the composition root fills from the llm module.
- ``SearchProvider``: the port each search provider implements; ``Searxng`` (internal,
  self-hosted) and ``LangSearch`` (external, API key) are the adapters.
- ``Network`` (``HttpRequest``, ``HttpReply``, ``NetworkError``): the network as a port;
  ``SystemNetwork`` is the real one, tests fake it, so CI never reaches a provider.
  ``fetch_page`` and ``is_public_address`` are the fetch and its SSRF guard;
  ``read_through_crawl4ai`` is the adapter for the optional browser inside the installation,
  the first fallback for a page ``fetch`` could not read; ``read_through_jina`` is the Jina
  Reader adapter after it (never for an internal-only Workspace or a URL with a query string).
- ``router``: ``/admin/web`` and ``/admin/web/providers/{kind}[/test]``, admins only.

Owns the ``web_settings`` and ``web_search_providers`` tables. Imports ``auth``, ``audit``
and ``workspaces`` (for the admin policy).
"""

from .api import router
from .internal.crawl4ai import read_through_crawl4ai
from .internal.fetch import FetchedPage, FetchError, fetch_page, is_public_address
from .internal.jina import read_through_jina
from .internal.network import HttpReply, HttpRequest, Network, NetworkError, SystemNetwork
from .internal.providers import LangSearch, SearchError, SearchProvider, SearchResult, Searxng
from .internal.strip import strip_values
from .service import WebProvider, WebSearch, WebService, WebSettings

__all__ = [
    "FetchError",
    "FetchedPage",
    "HttpReply",
    "HttpRequest",
    "LangSearch",
    "Network",
    "NetworkError",
    "SearchError",
    "SearchProvider",
    "SearchResult",
    "Searxng",
    "SystemNetwork",
    "WebProvider",
    "WebSearch",
    "WebService",
    "WebSettings",
    "fetch_page",
    "is_public_address",
    "read_through_crawl4ai",
    "read_through_jina",
    "router",
    "strip_values",
]
