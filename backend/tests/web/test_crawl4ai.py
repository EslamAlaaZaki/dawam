"""The Crawl4AI adapter: the request it sends and what counts as a failed read. The service is
a fake ``Network``: no Crawl4AI server and no page is ever reached."""

from __future__ import annotations

import json

import pytest

from dawam.modules.web import FetchError, NetworkError, read_through_crawl4ai
from dawam.modules.web.internal.fetch import BLOCK_PHRASES
from tests.web.fakes import FakeNetwork, reply

SERVICE = "http://crawl4ai:11235"
CRAWL = f"{SERVICE}/crawl"
PAGE = "https://docs.example.com/guide?tab=1"


def result(text="Facts and dimensions.", *, title="Guide", **fields) -> dict:
    return {
        "url": PAGE,
        "success": True,
        "status_code": 200,
        "markdown": {"raw_markdown": text, "fit_markdown": "not this one"},
        "metadata": {"title": title},
        **fields,
    }


def crawl_reply(*results, status=200, success=True):
    body = {"success": success, "results": list(results)}
    return reply(json.dumps(body), status, content_type="application/json")


def service(answer) -> FakeNetwork:
    return FakeNetwork(pages={CRAWL: answer})


def read(network, **options):
    return read_through_crawl4ai(network, SERVICE, PAGE, **options)


def failed(network) -> FetchError:
    with pytest.raises(FetchError) as raised:
        read(network)
    assert raised.value.code == "reader_failed"
    return raised.value


def sent_body(network) -> dict:
    return json.loads(network.sent[0].body)


def test_a_read_returns_the_raw_markdown_and_the_title():
    page = read(service(crawl_reply(result("## Star schemas\n\nFacts."))))

    assert page.url == PAGE
    assert page.title == "Guide"
    assert page.text == "## Star schemas\n\nFacts."
    assert page.content_type == "text/markdown"
    assert page.reader == "crawl4ai"
    assert not page.truncated


def test_the_url_is_where_the_browser_ended_up():
    page = read(service(crawl_reply(result(redirected_url="https://docs.example.com/moved"))))

    assert page.url == "https://docs.example.com/moved"


@pytest.mark.parametrize("where", ["file:///etc/passwd", "x" * 3000, None, 7])
def test_an_unusable_redirect_url_is_replaced_by_the_one_asked_for(where):
    page = read(service(crawl_reply(result(redirected_url=where))))

    assert page.url == PAGE


def test_the_request_is_one_post_to_the_service_with_the_url_and_query_string():
    network = service(crawl_reply(result()))

    read(network)

    [sent] = network.sent
    assert (sent.method, sent.url) == ("POST", CRAWL)
    assert sent.address is None  # the service is the operator's: no address is pinned
    assert sent.headers["Content-Type"] == "application/json"
    assert sent_body(network)["urls"] == [PAGE]


def test_the_service_address_is_not_checked_as_a_public_address():
    network = service(crawl_reply(result()))  # no host resolves on this network

    assert read(network).text


def test_the_request_carries_the_noise_options():
    network = service(crawl_reply(result()))

    read(network)

    config = sent_body(network)["crawler_config"]
    assert config["type"] == "CrawlerRunConfig"
    params = config["params"]
    assert params["wait_until"] == "domcontentloaded"
    assert params["page_timeout"] == 20000
    assert params["delay_before_return_html"] == 4.0
    assert params["excluded_tags"] == [
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "script",
        "style",
        "noscript",
        "iframe",
    ]
    assert params["markdown_generator"] == {
        "type": "DefaultMarkdownGenerator",
        "params": {
            "options": {"type": "dict", "value": {"ignore_links": True, "ignore_images": True}}
        },
    }


def test_the_request_carries_no_identity_proxy_or_captcha_option():
    network = service(crawl_reply(result()))

    read(network)

    body = sent_body(network)
    assert body["browser_config"] == {}  # the full browser, with Crawl4AI's default identity
    sent = network.sent[0].body.decode()
    for forbidden in (
        "magic",
        "enable_stealth",
        "simulate_user",
        "override_navigator",
        "light_mode",
        "text_mode",
        "proxy",
        "captcha",
        "AsyncHTTPCrawlerStrategy",
        "PruningContentFilter",
        "hooks",
    ):
        assert forbidden not in sent


def test_the_timeout_defaults_to_25_seconds_and_the_caller_can_lower_it():
    network = service(crawl_reply(result()))

    read(network)
    read(network, timeout=7.5)

    assert [r.timeout for r in network.sent] == [25.0, 7.5]


def test_the_reply_is_read_up_to_2_mb():
    network = service(crawl_reply(result()))

    read(network)

    assert network.sent[0].max_bytes == 2_000_000


def test_without_a_token_there_is_no_authorization_header():
    network = service(crawl_reply(result()))

    read(network)

    assert "Authorization" not in network.sent[0].headers


def test_a_token_is_sent_as_a_bearer_token_and_not_shown_in_the_request_repr():
    network = service(crawl_reply(result()))

    read(network, api_token="c4a-secret")

    assert network.sent[0].headers["Authorization"] == "Bearer c4a-secret"
    assert "c4a-secret" not in repr(network.sent[0])


def test_the_text_is_cut_to_the_limit_and_flagged():
    page = read(service(crawl_reply(result("x" * 13_000))))

    assert len(page.text) == 12_000
    assert page.truncated


def test_a_very_long_title_is_cut():
    page = read(service(crawl_reply(result(title="t" * 5_000))))

    assert len(page.title) == 300


def test_a_page_without_a_title_has_none():
    answer = result()
    del answer["metadata"]

    assert read(service(crawl_reply(answer))).title is None


def test_markdown_given_as_a_plain_string_is_read_too():
    answer = result()
    answer["markdown"] = "Plain text."

    assert read(service(crawl_reply(answer))).text == "Plain text."


@pytest.mark.parametrize(
    "fields",
    [
        {"success": False},
        {"status_code": 400},
        {"status_code": 403},
        {"status_code": 401},
        {"status_code": 500},
        {"redirected_status_code": 404},
    ],
    ids=["reports-failure", "400", "403", "401", "500", "final-hop-404"],
)
def test_a_reported_failure_or_a_bad_status_is_a_failed_read(fields):
    failed(service(crawl_reply(result(**fields))))


@pytest.mark.parametrize("text", ["", "   \n "])
def test_empty_text_is_a_failed_read(text):
    failed(service(crawl_reply(result(text))))


@pytest.mark.parametrize("phrase", BLOCK_PHRASES)
def test_each_block_phrase_in_the_title_is_a_failed_read(phrase):
    failed(service(crawl_reply(result("Plenty of text.", title=f"Oops: {phrase.title()}"))))


@pytest.mark.parametrize("phrase", BLOCK_PHRASES)
def test_each_block_phrase_at_the_start_of_the_text_is_a_failed_read(phrase):
    failed(service(crawl_reply(result(f"Sorry. {phrase.upper()}. " + "More text. " * 200))))


def test_a_block_phrase_deep_in_the_text_is_not_a_block_page():
    text = "A real article. " * 60 + "How a captcha works."

    assert read(service(crawl_reply(result(text)))).text == text


def test_the_block_phrases_are_one_constant_with_the_tickets_list():
    assert set(BLOCK_PHRASES) == {
        "access denied",
        "just a moment",
        "attention required",
        "security verification",
        "request blocked",
        "could not be satisfied",
        "verify you",
        "not a robot",
        "prove your humanity",
        "human verification",
        "been blocked",
        "captcha",
        "enable javascript",
        "please enable js",
    }


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502, 504])
def test_an_error_from_the_service_is_a_failed_read(status):
    failed(service(crawl_reply(result(), status=status)))


def test_a_reply_that_is_not_all_success_is_a_failed_read():
    failed(service(crawl_reply(result(), success=False)))


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"success": true}',
        '{"success": true, "results": []}',
        '{"success": true, "results": ["x"]}',
        '{"success": true, "results": [{"success": true}]}',
        '{"success": true, "results": [{"success": true, "markdown": {"raw_markdown": 3}}]}',
    ],
)
def test_an_unusable_reply_is_a_failed_read(body):
    failed(service(reply(body, content_type="application/json")))


def test_a_reply_over_2_mb_is_a_failed_read_not_half_a_document():
    huge = json.dumps({"success": True, "results": [result("x" * 2_100_000)]})

    failed(service(reply(huge, content_type="application/json")))


def test_an_unreachable_service_and_a_timeout_are_failed_reads_without_a_retry():
    def too_slow(request):
        raise NetworkError("The server did not answer in time.", timeout=True)

    unreachable = FakeNetwork()  # nothing answers at the service's address
    slow = service(too_slow)

    assert "not reached" in failed(unreachable).message
    assert "in time" in failed(slow).message
    assert len(unreachable.sent) == 1
    assert len(slow.sent) == 1


def test_the_failure_never_quotes_the_service_or_the_token():
    network = service(crawl_reply(result(success=False, error_message="secret internal detail")))

    with pytest.raises(FetchError) as raised:
        read(network, api_token="c4a-secret")

    assert "secret" not in raised.value.message
    assert SERVICE not in raised.value.message
