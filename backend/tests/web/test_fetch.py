"""Web fetch: SSRF blocking (after DNS and on every redirect), size, type and time caps."""

from __future__ import annotations

import http.server
import threading

import pytest

from dawam.modules.web import FetchError, HttpRequest, SystemNetwork, fetch_page, is_public_address
from tests.web.fakes import FakeNetwork, reply

PUBLIC = "93.184.215.14"


def site(**pages):
    return FakeNetwork(hosts={"docs.example.com": [PUBLIC]}, pages=pages)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.9.10",
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fe80::1",
        "fd00:ec2::254",  # AWS metadata over IPv6
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "2002:7f00:1::",  # 6to4 of 127.0.0.1
        "::",
    ],
)
def test_private_loopback_link_local_and_metadata_addresses_are_not_public(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_internet_addresses_are_public(address):
    assert is_public_address(address)


def test_a_public_page_is_fetched_from_the_checked_address_as_text():
    network = site(
        **{
            "https://docs.example.com/kimball": reply(
                "<html><head><title>Kimball</title><style>p{}</style></head>"
                "<body><script>evil()</script><h1>Star schemas</h1><p>Facts &amp; dims.</p>"
                "</body></html>"
            )
        }
    )

    page = fetch_page(network, "https://docs.example.com/kimball")

    assert page.url == "https://docs.example.com/kimball"
    assert page.title == "Kimball"
    assert "Star schemas" in page.text and "Facts & dims." in page.text
    assert "evil" not in page.text and "p{}" not in page.text
    assert network.sent[0].address == PUBLIC  # connected to the address it checked


@pytest.mark.parametrize(
    "addresses",
    [["10.0.0.5"], ["169.254.169.254"], [PUBLIC, "127.0.0.1"], ["::ffff:10.0.0.1"]],
)
def test_a_host_that_resolves_to_a_private_address_is_refused_before_connecting(addresses):
    network = FakeNetwork(
        hosts={"intranet.example.com": addresses},
        pages={"http://intranet.example.com/": reply("secret")},
    )

    with pytest.raises(FetchError) as caught:
        fetch_page(network, "http://intranet.example.com/")

    assert caught.value.code == "address_blocked"
    assert network.sent == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost/",
        "http://2130706433/",  # 127.0.0.1 as one number
        "http://0x7f.1/",
    ],
)
def test_literal_and_encoded_private_addresses_are_refused(url):
    # The fake resolves like the system would: the guard judges the resolved address.
    network = FakeNetwork(
        hosts={
            "127.0.0.1": ["127.0.0.1"],
            "::1": ["::1"],
            "169.254.169.254": ["169.254.169.254"],
            "localhost": ["127.0.0.1", "::1"],
            "2130706433": ["127.0.0.1"],
            "0x7f.1": ["127.0.0.1"],
        }
    )

    with pytest.raises(FetchError) as caught:
        fetch_page(network, url)

    assert caught.value.code == "address_blocked"
    assert network.sent == []


def test_a_redirect_to_a_private_address_is_refused():
    network = FakeNetwork(
        hosts={"docs.example.com": [PUBLIC], "metadata.internal": ["169.254.169.254"]},
        pages={
            "https://docs.example.com/go": reply(
                status=302, headers={"location": "http://metadata.internal/latest/"}
            ),
            "http://metadata.internal/latest/": reply("credentials"),
        },
    )

    with pytest.raises(FetchError) as caught:
        fetch_page(network, "https://docs.example.com/go")

    assert caught.value.code == "address_blocked"
    assert [r.url for r in network.sent] == ["https://docs.example.com/go"]


def test_a_relative_redirect_is_followed_and_checked_again():
    network = site(
        **{
            "https://docs.example.com/old": reply(status=301, headers={"location": "/new"}),
            "https://docs.example.com/new": reply("<p>moved here</p>"),
        }
    )

    page = fetch_page(network, "https://docs.example.com/old")

    assert page.url == "https://docs.example.com/new"
    assert "moved here" in page.text


def test_too_many_redirects_stop():
    network = site(
        **{"https://docs.example.com/loop": reply(status=302, headers={"location": "/loop"})}
    )

    with pytest.raises(FetchError) as caught:
        fetch_page(network, "https://docs.example.com/loop")

    assert caught.value.code == "too_many_redirects"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://docs.example.com/",
        "gopher://docs.example.com/",
        "https://user:pass@docs.example.com/",
        "https://docs.example.com:6379/",
        "not a url",
    ],
)
def test_other_schemes_ports_and_credentials_are_refused(url):
    network = site()

    with pytest.raises(FetchError) as caught:
        fetch_page(network, url)

    assert caught.value.code == "invalid_url"
    assert network.sent == []


def test_only_text_types_are_read():
    network = site(
        **{"https://docs.example.com/a.zip": reply(b"PK..", content_type="application/zip")}
    )

    with pytest.raises(FetchError) as caught:
        fetch_page(network, "https://docs.example.com/a.zip")

    assert caught.value.code == "unsupported_type"


def test_a_large_page_is_cut_at_the_size_cap():
    network = site(**{"https://docs.example.com/big": reply("<p>" + "x" * 5000 + "</p>")})

    page = fetch_page(network, "https://docs.example.com/big", max_bytes=1000)

    assert network.sent[0].max_bytes == 1000
    assert page.truncated


def test_an_error_status_is_reported():
    network = site(**{"https://docs.example.com/gone": reply("nope", status=404)})

    with pytest.raises(FetchError) as caught:
        fetch_page(network, "https://docs.example.com/gone")

    assert caught.value.code == "http_error"


def test_the_system_network_connects_to_the_pinned_address():
    """The real transport: the URL names a host that does not resolve, the pinned address is
    a local server, so the connection went where the guard said (the guard itself is above)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"host={self.headers['Host']}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        got = SystemNetwork().send(
            HttpRequest("GET", f"http://unresolvable.invalid:{port}/x", address="127.0.0.1")
        )
    finally:
        server.shutdown()

    assert got.status == 200
    assert got.body == f"host=unresolvable.invalid:{port}".encode()
