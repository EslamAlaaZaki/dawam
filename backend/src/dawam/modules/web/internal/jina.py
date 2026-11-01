"""Jina Reader: an outside service that reads a page DAWAM's own fetch could not (ADR 0008).

The URL leaves the installation, so this is the caller's decision, not this adapter's: only
a URL that passed the address checks, has no query string and belongs to a Workspace that is
not internal-only comes here (``WebService.fetch``). Works without a key; the optional key
goes in the ``Authorization`` header and nowhere else. Jina answers HTTP 200 even when the
site refused it, so a ``Warning:`` line above the text or no text at all is a failed read.
"""

from __future__ import annotations

import re

from .fetch import (
    MAX_FETCH_BYTES,
    MAX_TEXT_CHARS,
    MAX_URL_LENGTH,
    USER_AGENT,
    FetchedPage,
    FetchError,
    decoded,
)
from .network import HttpReply, HttpRequest, Network, NetworkError

READER = "jina"
READER_URL = "https://r.jina.ai/"
READER_TIMEOUT_SECONDS = 30.0
NOISE_HEADERS = {
    "X-Retain-Images": "none",
    "X-Md-Link-Style": "discarded",
    "X-Remove-Selector": (
        "nav,header,footer,aside,form,script,style,noscript,iframe,"
        "[role=navigation],[role=banner],[role=contentinfo]"
    ),
}

_CONTENT_MARKER = re.compile(r"^Markdown Content:[ \t]*\r?$", re.MULTILINE)
_WARNING = re.compile(r"^Warning:", re.MULTILINE)
_TITLE = re.compile(r"^Title:[ \t]*(.*?)[ \t]*\r?$", re.MULTILINE)
_SOURCE = re.compile(r"^URL Source:[ \t]*(\S*)[ \t]*\r?$", re.MULTILINE)


def _failed(reason: str) -> FetchError:
    # Static text: neither the key nor Jina's own words reach the model.
    return FetchError("reader_failed", f"The page could not be read through Jina: {reason}.")


def read_through_jina(network: Network, url: str, *, api_key: str | None = None) -> FetchedPage:
    """One read of ``url``, never retried; ``reader_failed`` when it gave no page."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/plain", **NOISE_HEADERS}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        got = network.send(
            HttpRequest(
                "GET",
                f"{READER_URL}{url}",
                headers=headers,
                timeout=READER_TIMEOUT_SECONDS,
                max_bytes=MAX_FETCH_BYTES,
            )
        )
    except NetworkError as exc:
        raise _failed(
            "it did not answer in time" if exc.timeout else "it was not reached"
        ) from None
    if got.status != 200:
        raise _failed(f"it answered with HTTP {got.status}")
    return _page(got, url)


def _page(got: HttpReply, url: str) -> FetchedPage:
    raw = decoded(got)
    marker = _CONTENT_MARKER.search(raw)
    if marker is None:
        raise _failed("it returned no text")
    header, text = raw[: marker.start()], raw[marker.end() :].strip()
    # The header only: a page may well have a line of its own that starts with "Warning:".
    if _WARNING.search(header):
        raise _failed("the site refused it")
    if not text:
        raise _failed("it returned no text")
    title = _TITLE.search(header)
    return FetchedPage(
        _source(header) or url,
        "text/markdown",
        (title.group(1) or None) if title else None,
        text[:MAX_TEXT_CHARS],
        got.truncated or len(text) > MAX_TEXT_CHARS,
        reader=READER,
    )


def _source(header: str) -> str | None:
    """Where Jina says it read the page, if that is a web address."""
    match = _SOURCE.search(header)
    source = match.group(1) if match else ""
    if source.startswith(("http://", "https://")) and len(source) <= MAX_URL_LENGTH:
        return source
    return None
