"""Crawl4AI: a browser inside the installation that reads a page DAWAM's own fetch could not
(ADR 0008, #545).

The server runs on the operator's locked-down network (``deploy/README.md``), so its address
is operator configuration and is not checked as a public address. The page's URL is not: only
a URL that passed the address checks comes here (``WebService.fetch``). A query string may
stay, since the target site would get that URL from DAWAM's own fetch anyway.

The options are the request shape of the pinned image (``unclecode/crawl4ai:0.9.5``): nothing
sets the browser's identity, and the server refuses a request body that sets a proxy. The
server reports success for a 403 or a CAPTCHA page, so a read fails on a reported failure, a
status of 400 or more, empty text or a block page. No failure quotes the service's words.
"""

from __future__ import annotations

import json
from typing import Any

from .fetch import (
    MAX_FETCH_BYTES,
    MAX_TEXT_CHARS,
    MAX_TITLE_CHARS,
    MAX_URL_LENGTH,
    FetchedPage,
    FetchError,
    looks_blocked,
)
from .network import HttpReply, HttpRequest, Network, NetworkError

READER = "crawl4ai"
READER_TIMEOUT_SECONDS = 25.0
CRAWLER_CONFIG = {
    "type": "CrawlerRunConfig",
    "params": {
        "wait_until": "domcontentloaded",
        "page_timeout": 20000,
        "delay_before_return_html": 4.0,
        "excluded_tags": [
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "script",
            "style",
            "noscript",
            "iframe",
        ],
        "markdown_generator": {
            "type": "DefaultMarkdownGenerator",
            "params": {
                "options": {"type": "dict", "value": {"ignore_links": True, "ignore_images": True}}
            },
        },
    },
}
"""Sent on every request: it removes most of a page's furniture (#545)."""


def _failed(reason: str) -> FetchError:
    # Static text: neither the token nor Crawl4AI's own words reach the model.
    return FetchError("reader_failed", f"The page could not be read through Crawl4AI: {reason}.")


def read_through_crawl4ai(
    network: Network,
    service_url: str,
    url: str,
    *,
    api_token: str | None = None,
    timeout: float = READER_TIMEOUT_SECONDS,
) -> FetchedPage:
    """One read of ``url``, never retried; ``reader_failed`` when it gave no page."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    body = {"urls": [url], "browser_config": {}, "crawler_config": CRAWLER_CONFIG}
    try:
        got = network.send(
            HttpRequest(
                "POST",
                f"{service_url}/crawl",
                headers=headers,
                body=json.dumps(body).encode(),
                timeout=timeout,
                max_bytes=MAX_FETCH_BYTES,
            )
        )
    except NetworkError as exc:
        raise _failed(
            "it did not answer in time" if exc.timeout else "it was not reached"
        ) from None
    if got.status != 200:
        raise _failed(f"it answered with HTTP {got.status}")
    return _page(_first_result(got), url)


def _first_result(got: HttpReply) -> dict[str, Any]:
    if got.truncated:
        raise _failed("its reply was too large")  # half a JSON document
    try:
        reply = json.loads(got.body)
    except ValueError:
        raise _failed("its reply could not be read") from None
    results = reply.get("results") if isinstance(reply, dict) else None
    if not reply or not isinstance(results, list) or not results:
        raise _failed("its reply could not be read")
    first = results[0]
    if not isinstance(first, dict):
        raise _failed("its reply could not be read")
    if reply.get("success") is not True or first.get("success") is not True:
        raise _failed("the site refused it")
    return first


def _page(result: dict[str, Any], url: str) -> FetchedPage:
    for field in ("status_code", "redirected_status_code"):
        status = result.get(field)
        if isinstance(status, int) and status >= 400:
            raise _failed(f"the site answered with HTTP {status}")
    text = _raw_markdown(result.get("markdown")).strip()
    if not text:
        raise _failed("it returned no text")
    metadata = result.get("metadata")
    title = metadata.get("title") if isinstance(metadata, dict) else None
    title = title[:MAX_TITLE_CHARS] or None if isinstance(title, str) else None
    if looks_blocked(title, text):
        raise _failed("the site refused it")
    return FetchedPage(
        _final_url(result) or url,
        "text/markdown",
        title,
        text[:MAX_TEXT_CHARS],
        len(text) > MAX_TEXT_CHARS,
        reader=READER,
    )


def _raw_markdown(markdown: object) -> str:
    """The raw Markdown: the reply nests it, and an older reply gave a plain string."""
    if isinstance(markdown, dict):
        markdown = markdown.get("raw_markdown")
    return markdown if isinstance(markdown, str) else ""


def _final_url(result: dict[str, Any]) -> str | None:
    """Where the browser ended up, if that is a web address."""
    where = result.get("redirected_url")
    if (
        isinstance(where, str)
        and where.startswith(("http://", "https://"))
        and len(where) <= MAX_URL_LENGTH
    ):
        return where
    return None
