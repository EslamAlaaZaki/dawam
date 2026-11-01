"""Web fetch: SSRF blocking (after DNS and on every redirect), size, type and time caps."""

from __future__ import annotations

import http.server
import threading
import time

import pytest

from dawam.modules.web import (
    FetchError,
    HttpRequest,
    NetworkError,
    SystemNetwork,
    fetch_page,
    is_public_address,
)
from dawam.modules.web.internal.network import _read
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


def failing_fetch(network, url) -> FetchError:
    with pytest.raises(FetchError) as caught:
        fetch_page(network, url)
    return caught.value


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_an_error_status_may_be_read_through_the_reader(status):
    network = site(**{"https://docs.example.com/a": reply("no", status=status)})

    assert failing_fetch(network, "https://docs.example.com/a").reader_may_try


def test_a_timeout_after_resolving_may_be_read_through_the_reader():
    def too_slow(request):
        raise NetworkError("The server did not answer in time.", timeout=True)

    error = failing_fetch(
        site(**{"https://docs.example.com/a": too_slow}), "https://docs.example.com/a"
    )

    assert error.code == "timeout"
    assert error.reader_may_try


def test_a_failed_connection_after_resolving_may_be_read_through_the_reader():
    error = failing_fetch(site(), "https://docs.example.com/a")  # resolves, no page answers

    assert error.code == "unreachable"
    assert error.reader_may_try


def test_a_host_that_does_not_resolve_may_not_be_read_through_the_reader():
    # The address could not be checked: an internal name must not leave the network.
    network = FakeNetwork()

    error = failing_fetch(network, "https://wiki.corp.internal/a")

    assert error.code == "unreachable"
    assert not error.reader_may_try
    assert network.sent == []


def test_a_redirect_to_a_host_that_does_not_resolve_may_not_be_read_through_the_reader():
    network = site(
        **{
            "https://docs.example.com/go": reply(
                status=302, headers={"location": "https://wiki.corp.internal/a"}
            )
        }
    )

    assert not failing_fetch(network, "https://docs.example.com/go").reader_may_try


@pytest.mark.parametrize(
    ("url", "pages"),
    [
        ("https://user:pass@docs.example.com/", {}),  # invalid_url
        (
            "https://docs.example.com/a.zip",
            {"https://docs.example.com/a.zip": reply(b"PK", content_type="application/zip")},
        ),
        (
            "https://docs.example.com/loop",
            {"https://docs.example.com/loop": reply(status=302, headers={"location": "/loop"})},
        ),
        ("https://docs.example.com/where", {"https://docs.example.com/where": reply(status=302)}),
        (
            "https://docs.example.com/up",
            {"https://docs.example.com/up": reply(status=302, headers={"location": "ftp://x/"})},
        ),
    ],
)
def test_the_other_failures_may_not_be_read_through_the_reader(url, pages):
    assert not failing_fetch(site(**pages), url).reader_may_try


def test_a_hop_blocked_by_the_address_check_may_not_be_read_through_the_reader():
    network = FakeNetwork(
        hosts={"docs.example.com": [PUBLIC], "metadata.internal": ["169.254.169.254"]},
        pages={
            "https://docs.example.com/go": reply(
                status=302, headers={"location": "http://metadata.internal/latest/"}
            ),
        },
    )

    error = failing_fetch(network, "https://docs.example.com/go")

    assert error.code == "address_blocked"
    assert not error.reader_may_try


def test_a_page_dawam_read_itself_says_so():
    page = fetch_page(
        site(**{"https://docs.example.com/a": reply("<p>hi</p>")}), "https://docs.example.com/a"
    )

    assert page.reader == "dawam"


def test_a_header_the_system_network_refuses_is_a_network_error_that_does_not_quote_it():
    # http.client's own message quotes the bad value: a mistyped key must not reach a log.
    request = HttpRequest(
        "GET",
        "http://unresolvable.invalid/x",
        headers={"Authorization": "Bearer jina\nsecret"},
        address="127.0.0.1",
    )

    with pytest.raises(NetworkError) as caught:
        SystemNetwork().send(request)

    assert "secret" not in str(caught.value)


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


# --- Other addresses of a site (#538) ---


def by_address(**answers):
    """A page that answers by the address connected to; an exception is raised."""

    def respond(request):
        got = answers[request.address]
        if isinstance(got, Exception):
            raise got
        return got

    return respond


SLOW = NetworkError("The server did not answer in time.", timeout=True)
URL = "https://multi.example.com/a"


def multi(addresses, **answers):
    return FakeNetwork(hosts={"multi.example.com": addresses}, pages={URL: by_address(**answers)})


def test_the_next_address_is_tried_when_the_first_times_out():
    network = multi(["8.8.8.8", PUBLIC], **{"8.8.8.8": SLOW, PUBLIC: reply("<p>hello</p>")})

    page = fetch_page(network, URL)

    assert page.text == "hello"
    assert [r.address for r in network.sent] == ["8.8.8.8", PUBLIC]


def test_an_http_reply_ends_the_attempts_for_the_hop():
    network = multi(["8.8.8.8", PUBLIC], **{"8.8.8.8": reply("no", status=403), PUBLIC: reply("x")})

    error = failing_fetch(network, URL)

    assert error.code == "http_error"
    assert [r.address for r in network.sent] == ["8.8.8.8"]


def test_one_private_address_refuses_the_host_before_any_attempt():
    network = multi(["8.8.8.8", "10.0.0.1"], **{"8.8.8.8": reply("x")})

    assert failing_fetch(network, URL).code == "address_blocked"
    assert network.sent == []


def test_a_private_fourth_address_refuses_the_host_before_any_attempt():
    addresses = ["8.8.8.8", "8.8.4.4", "1.1.1.1", "10.0.0.1"]
    network = multi(addresses, **dict.fromkeys(addresses, reply("x")))

    assert failing_fetch(network, URL).code == "address_blocked"
    assert network.sent == []


def test_a_hop_resolves_once_and_tries_at_most_three_addresses():
    dead = ["8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1"]
    network = multi(dead, **dict.fromkeys(dead, SLOW))
    lookups = []
    resolve = network.resolve
    network.resolve = lambda host, port: lookups.append(host) or resolve(host, port)

    error = failing_fetch(network, URL)

    assert len(lookups) == 1
    assert [r.address for r in network.sent] == ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
    assert (error.code, error.reader_may_try) == ("timeout", True)


def test_every_address_failing_keeps_the_last_error_and_the_reader_fallback():
    refused = NetworkError("The server could not be reached.")
    network = multi(["8.8.8.8", PUBLIC], **{"8.8.8.8": SLOW, PUBLIC: refused})

    error = failing_fetch(network, URL)

    assert (error.code, error.reader_may_try) == ("unreachable", True)


def test_a_hop_spends_at_most_twenty_seconds(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("dawam.modules.web.internal.fetch.time.monotonic", lambda: clock[0])

    def slow(request):
        clock[0] += request.timeout
        raise SLOW

    network = FakeNetwork(hosts={"multi.example.com": ["8.8.8.8", "8.8.4.4", "1.1.1.1"]})
    network.pages[URL] = slow

    failing_fetch(network, URL)

    assert [r.address for r in network.sent] == ["8.8.8.8", "8.8.4.4"]
    assert sum(r.timeout for r in network.sent) <= 20.0


def test_a_deadline_shortens_a_hop_and_running_out_is_a_timeout_a_reader_may_follow():
    clock = [0.0]

    def slow(request):
        clock[0] += request.timeout
        raise SLOW

    network = FakeNetwork(hosts={"multi.example.com": ["8.8.8.8", "8.8.4.4", "1.1.1.1"]})
    network.pages[URL] = slow

    with pytest.raises(FetchError) as raised:
        fetch_page(network, URL, deadline=14.0, clock=lambda: clock[0])

    assert [r.timeout for r in network.sent] == [10.0, 4.0]
    assert (raised.value.code, raised.value.reader_may_try) == ("timeout", True)


def test_a_redirect_past_the_deadline_sends_nothing_more():
    clock = [0.0]
    first = "https://a.example.com/start"
    second = "https://a.example.com/next"

    def moved(request):
        clock[0] = 45.0
        return reply("", 302, {"location": second})

    network = FakeNetwork(hosts={"a.example.com": ["8.8.8.8"]}, pages={first: moved})

    with pytest.raises(FetchError) as raised:
        fetch_page(network, first, deadline=45.0, clock=lambda: clock[0])

    assert [r.url for r in network.sent] == [first]
    assert (raised.value.code, raised.value.reader_may_try) == ("timeout", True)


def test_a_slow_body_cannot_outlast_the_whole_exchanges_timeout():
    class Silence:
        def __init__(self):
            self.timeouts = []

        def fileno(self):
            return 7

        def settimeout(self, value):
            self.timeouts.append(value)

    class Trickle:
        def read1(self, _size):
            return b"x"

    with pytest.raises(TimeoutError):
        _read(Trickle(), 10**9, time.monotonic() + 0.05, Silence())


# -- page furniture and thin pages (#547) ---------------------------------------------------

MIN_USEFUL = 1500  # MIN_USEFUL_TEXT_CHARS
LIMIT = 12_000  # MAX_TEXT_CHARS


def page_of(body: str, *, content_type: str = "text/html; charset=utf-8"):
    network = site(**{"https://docs.example.com/p": reply(body, content_type=content_type)})
    return fetch_page(network, "https://docs.example.com/p")


def text_of(html: str) -> str:
    return page_of(html).text


def test_nav_footer_aside_and_a_page_level_header_are_dropped():
    html = (
        "<body><header>Site banner</header><nav><ul><li>Menu item</li></ul></nav>"
        "<main><p>The real content.</p><aside>Related links</aside></main>"
        "<footer>Copyright</footer></body>"
    )

    assert text_of(html) == "The real content."


def test_the_header_of_an_article_or_section_is_kept():
    html = (
        "<header>Banner</header>"
        "<article><header><h1>Title</h1></header><p>Body.</p></article>"
        "<section><header>Part one</header><p>More.</p></section>"
    )

    text = text_of(html)

    assert "Title" in text and "Part one" in text and "Banner" not in text


def test_furniture_nested_in_furniture_is_dropped_once_and_the_page_goes_on():
    assert text_of("<nav><div><nav>inner</nav>outer</div></nav><p>After.</p>") == "After."


def test_a_furniture_tag_left_open_does_not_lose_the_rest_of_the_page():
    assert "Rest of the page" in text_of("<div><nav>Menu</div><p>Rest of the page</p>")
    assert "Rest of the page" in text_of("<body><nav>Menu<main><p>Rest of the page</p>")
    # Never closed and nothing says where it ends: nothing is lost.
    assert "Rest of the page" in text_of("<aside>Menu<p>Rest of the page</p>")


def test_a_parent_that_closes_open_furniture_gives_back_what_it_held():
    html = "<div id=app><nav>Menu<p>Real article</p></div><p>After.</p>"

    text = text_of(html)

    assert "Real article" in text and "After." in text


def test_furniture_closed_by_its_own_end_tag_is_still_dropped_inside_a_parent():
    assert text_of("<div><nav>Menu</nav><p>Kept.</p></div>") == "Kept."


def test_the_header_of_main_is_kept():
    assert "Title" in text_of("<header>Banner</header><main><header><h1>Title</h1></header></main>")


def test_deeply_nested_or_unclosed_tags_keep_all_text_and_cost_linear_time():
    # Old code scanned the open-tag list on every end tag: this body took minutes.
    unclosed = "<p>Head text" + "<p>x" * 150_000 + "</b>" * 300_000 + "<p>Tail text"
    nested = "<div>" * 2000 + "<nav>Deep menu</nav><p>Deep text</p>" + "</div>" * 2000

    assert text_of(unclosed).startswith("Head text")  # the page is read to its end, in time
    assert "Deep text" in text_of(nested)  # past the tracked depth nothing is dropped
    assert "Deep text" in text_of("<header>" + "<div>" * 2000 + "<p>Deep text</p>")


def test_a_stray_end_tag_and_unclosed_inner_tags_change_nothing():
    text = text_of("</nav><div><p>One<p>Two<li>Three</div><footer>Bye</footer><p>Four")

    assert "Bye" not in text and {"One", "Two", "Three", "Four"} <= set(text.split("\n"))


def test_html_under_the_useful_minimum_is_thin_and_at_it_is_not():
    assert page_of("<p>" + "a" * (MIN_USEFUL - 1) + "</p>").thin
    assert page_of("<nav>" + "a" * MIN_USEFUL + "</nav><p>short</p>").thin  # furniture: not text
    assert not page_of("<p>" + "a" * MIN_USEFUL + "</p>").thin


def test_the_minimum_is_measured_before_the_cut_and_only_for_html():
    assert not page_of("<p>" + "a" * (LIMIT + 500) + "</p>").thin
    for content_type, body in [
        ("text/plain", "short"),
        ("application/json", '{"a": 1}'),
        ("text/csv", "a,b"),
        ("application/xml", "<a/>"),
    ]:
        page = page_of(body, content_type=content_type)
        assert not page.thin and page.text == body
