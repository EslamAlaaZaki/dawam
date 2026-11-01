"""The Jina Reader adapter: the request it sends and what counts as a failed read. Jina is a
fake ``Network``: ``r.jina.ai`` is never reached."""

from __future__ import annotations

import pytest

from dawam.modules.web import FetchError, read_through_jina
from tests.web.fakes import FakeNetwork, reply

PAGE = "https://docs.example.com/guide"
READER = f"https://r.jina.ai/{PAGE}"


def jina_text(text="Facts and dimensions.", *, title="Guide", warning=None, source=PAGE) -> str:
    lines = [f"Title: {title}", "", f"URL Source: {source}", ""]
    if warning:
        lines += [f"Warning: {warning}", ""]
    return "\n".join([*lines, "Markdown Content:", text])


def jina(body, status=200) -> FakeNetwork:
    return FakeNetwork(pages={READER: reply(body, status, content_type="text/plain")})


def test_a_read_returns_the_title_source_and_text_after_the_marker():
    page = read_through_jina(jina(jina_text("## Star schemas\n\nFacts.")), PAGE)

    assert page.url == PAGE
    assert page.title == "Guide"
    assert page.text == "## Star schemas\n\nFacts."
    assert page.content_type == "text/markdown"
    assert page.reader == "jina"
    assert not page.truncated


def test_the_request_is_a_get_with_the_noise_headers_and_a_30_second_timeout():
    network = jina(jina_text())

    read_through_jina(network, PAGE)

    [sent] = network.sent
    assert (sent.method, sent.url) == ("GET", READER)
    assert sent.timeout == 30.0
    assert sent.address is None
    assert sent.headers["Accept"] == "text/plain"
    assert sent.headers["X-Retain-Images"] == "none"
    assert sent.headers["X-Md-Link-Style"] == "discarded"
    assert sent.headers["X-Remove-Selector"] == (
        "nav,header,footer,aside,form,script,style,noscript,iframe,"
        "[role=navigation],[role=banner],[role=contentinfo]"
    )


def test_without_a_key_there_is_no_authorization_header():
    network = jina(jina_text())

    read_through_jina(network, PAGE)

    assert "Authorization" not in network.sent[0].headers


def test_with_a_key_it_is_sent_as_a_bearer_token_and_not_shown_in_the_request_repr():
    network = jina(jina_text())

    read_through_jina(network, PAGE, api_key="jina-secret")

    assert network.sent[0].headers["Authorization"] == "Bearer jina-secret"
    assert "jina-secret" not in repr(network.sent[0])


def test_the_text_is_cut_to_the_limit_and_flagged():
    page = read_through_jina(jina(jina_text("x" * 13_000)), PAGE)

    assert len(page.text) == 12_000
    assert page.truncated


def test_a_very_long_title_is_cut():
    page = read_through_jina(jina(jina_text(title="t" * 5_000)), PAGE)

    assert page.title == "t" * 300


def test_a_body_cut_at_the_size_cap_is_flagged():
    page = read_through_jina(jina(jina_text("x" * 2_000_100)), PAGE)

    assert len(page.text) == 12_000
    assert page.truncated


@pytest.mark.parametrize(
    "body",
    [
        jina_text(warning="Target URL returned error 403: Forbidden"),
        jina_text("", warning="This page maybe not yet fully loaded"),
        jina_text(""),
        jina_text("   \n  "),
        "Title: Guide\n\nURL Source: https://docs.example.com/guide\n",  # no marker at all
        "",
    ],
)
def test_a_warning_or_empty_text_in_a_200_reply_is_a_failed_read(body):
    with pytest.raises(FetchError) as caught:
        read_through_jina(jina(body), PAGE)

    assert caught.value.code == "reader_failed"


def test_a_warning_line_inside_the_page_itself_is_just_text():
    body = jina_text("Intro.\nWarning: do not mix grains.\nMarkdown Content:\nMore.")

    page = read_through_jina(jina(body), PAGE)

    assert "Warning: do not mix grains." in page.text


@pytest.mark.parametrize("status", [301, 401, 429, 500, 503])
def test_any_other_status_is_a_failed_read_and_is_not_retried(status):
    network = jina(jina_text(), status)

    with pytest.raises(FetchError) as caught:
        read_through_jina(network, PAGE)

    assert caught.value.code == "reader_failed"
    assert len(network.sent) == 1


def test_a_timeout_or_lost_connection_is_a_failed_read_and_is_not_retried():
    network = FakeNetwork()  # r.jina.ai cannot be reached

    with pytest.raises(FetchError) as caught:
        read_through_jina(network, PAGE, api_key="jina-secret")

    assert caught.value.code == "reader_failed"
    assert "jina-secret" not in caught.value.message
    assert len(network.sent) == 1


@pytest.mark.parametrize("source", ["javascript:alert(1)", "", "ftp://docs.example.com/x"])
def test_a_source_that_is_no_web_address_falls_back_to_the_url_asked_for(source):
    page = read_through_jina(jina(jina_text(source=source)), PAGE)

    assert page.url == PAGE


def test_a_redirected_source_is_reported():
    moved = "https://docs.example.com/new-guide"

    page = read_through_jina(jina(jina_text(source=moved)), PAGE)

    assert page.url == moved
