"""Web fetch: read one page a member pointed the assistant at, safely (ADR 0008).

The URL comes from the model, so it is treated as hostile (SSRF): only ``http``/``https``
on the default ports, no credentials, and every address the host resolves to must be on
the public internet. The connection goes to an address that was checked (no second
lookup per hop); when it fails or times out, the next checked address is tried (at most
three, twenty seconds in all). Every redirect is checked the same way before it is followed.
The body is capped in size and time, only text types are read, and HTML is reduced to its
text.

A failure says whether the page may be read through the reader instead (``reader_may_try``,
ADR 0008): only when the address was checked and the site itself did not give the page. Off
unless a failure turns it on, so a new failure stays inside the installation by default.
"""

from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .network import HttpReply, HttpRequest, Network, NetworkError

MAX_FETCH_BYTES = 2_000_000
"""What is read of a page's body; the rest is cut."""
FETCH_TIMEOUT_SECONDS = 10.0
MAX_ADDRESS_ATTEMPTS = 3
"""Addresses tried for one request (redirect hop)."""
HOP_TIMEOUT_SECONDS = 20.0
"""All attempts for one hop together."""
MAX_REDIRECTS = 5
MAX_URL_LENGTH = 2000
MAX_TEXT_CHARS = 12_000
"""What of a page's text reaches the model."""
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
)
PORTS = {"http": 80, "https": 443}
USER_AGENT = "DAWAM-web-fetch/1.0"
OWN_READER = "dawam"

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class FetchError(Exception):
    def __init__(self, code: str, message: str, *, reader_may_try: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reader_may_try = reader_may_try


@dataclass(frozen=True)
class FetchedPage:
    url: str
    """Where the page was read, after redirects."""
    content_type: str
    title: str | None
    text: str
    truncated: bool
    reader: str = OWN_READER
    """Who read it: DAWAM itself or, through the fallback, ``jina``."""


def is_public_address(address: str) -> bool:
    """Is ``address`` on the public internet? Private, loopback, link-local (cloud
    metadata), shared, reserved and multicast ranges are not, nor is an IPv6 address that
    embeds one (IPv4-mapped, NAT64, 6to4, Teredo)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if ip.teredo:
            embedded = ip.teredo[1]
        if ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and not is_public_address(str(embedded)):
            return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _checked(url: str) -> tuple[str, int]:
    """The host and port of an acceptable URL, or ``invalid_url``."""
    invalid = FetchError(
        "invalid_url", "Only http:// and https:// URLs on the usual ports can be read."
    )
    if len(url) > MAX_URL_LENGTH:
        raise invalid
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise invalid from None
    if parts.scheme not in PORTS or not parts.hostname or parts.username or parts.password:
        raise invalid
    if port is not None and port != PORTS[parts.scheme]:
        raise invalid
    return parts.hostname, PORTS[parts.scheme]


def _public_addresses(network: Network, host: str, port: int) -> list[str]:
    """The addresses of ``host`` to try, in order, when every address it has is public."""
    blocked = FetchError(
        "address_blocked",
        "This address is on a private or internal network, which web fetch never reads.",
    )
    try:
        addresses = network.resolve(host, port)
    except NetworkError:
        raise FetchError("unreachable", "The host name could not be resolved.") from None
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise blocked
    return addresses[:MAX_ADDRESS_ATTEMPTS]


def fetch_page(
    network: Network,
    url: str,
    *,
    max_bytes: int = MAX_FETCH_BYTES,
    timeout: float = FETCH_TIMEOUT_SECONDS,
) -> FetchedPage:
    current = url.strip()
    for _ in range(MAX_REDIRECTS + 1):
        host, port = _checked(current)
        addresses = _public_addresses(network, host, port)  # one lookup for the whole hop
        deadline = time.monotonic() + max(HOP_TIMEOUT_SECONDS, timeout)
        got: HttpReply | None = None
        failure = NetworkError("The server could not be reached.")
        for attempt, address in enumerate(addresses):
            left = deadline - time.monotonic()
            if attempt and left <= 0:
                break
            try:
                got = network.send(
                    HttpRequest(
                        "GET",
                        current,
                        headers={"User-Agent": USER_AGENT, "Accept": ", ".join(TEXT_TYPES)},
                        timeout=min(timeout, left) if attempt else timeout,
                        max_bytes=max_bytes,
                        address=address,
                    )
                )
                break
            except NetworkError as exc:
                failure = exc  # only a failed connection moves on; any reply ends the hop
        if got is None:
            code = "timeout" if failure.timeout else "unreachable"
            raise FetchError(code, str(failure), reader_may_try=True) from None
        if got.status in (301, 302, 303, 307, 308):
            location = got.headers.get("location")
            if not location:
                raise FetchError("http_error", "The page redirected without saying where.")
            current = urljoin(current, location)
            continue
        if got.status >= 400:
            raise FetchError(
                "http_error", f"The server answered with HTTP {got.status}.", reader_may_try=True
            )
        content_type = got.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in TEXT_TYPES:
            raise FetchError(
                "unsupported_type",
                f"Web fetch reads text pages only, not `{content_type or 'unknown'}`.",
            )
        raw = decoded(got)
        title, text = _html_text(raw) if "html" in content_type else (None, raw.strip())
        truncated = got.truncated or len(text) > MAX_TEXT_CHARS
        return FetchedPage(current, content_type, title, text[:MAX_TEXT_CHARS], truncated)
    raise FetchError("too_many_redirects", f"The page redirected more than {MAX_REDIRECTS} times.")


def decoded(got: HttpReply) -> str:
    return got.body.decode(_charset(got.headers.get("content-type", "")), errors="replace")


def _charset(content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if match:
        try:
            "".encode(match.group(1))
            return match.group(1)
        except LookupError:
            pass
    return "utf-8"


class _TextOnly(HTMLParser):
    SKIP = frozenset({"script", "style", "noscript", "template", "svg"})
    BLOCK = frozenset(
        {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "pre"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self._skipping = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
        elif not self._skipping:
            self.parts.append(data)


def _html_text(html: str) -> tuple[str | None, str]:
    parser = _TextOnly()
    parser.feed(html)
    parser.close()
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    title = " ".join("".join(parser.title).split()) or None
    return title, text
