"""The search providers behind the port (faked network), and value stripping from queries."""

from __future__ import annotations

import json

import pytest

from dawam.modules.web import LangSearch, SearchError, Searxng, strip_values
from tests.web.fakes import FakeNetwork, reply

SEARXNG = {
    "query": "kimball scd type 2",
    "results": [
        {"url": "https://example.org/scd", "title": "SCD type 2", "content": "History rows."},
        {"url": "https://example.org/b", "title": "B", "content": ""},
        {"url": "javascript:alert(1)", "title": "bad", "content": "dropped"},
    ],
}

LANGSEARCH = {
    "code": 200,
    "msg": None,
    "data": {
        "webPages": {
            "value": [
                {
                    "name": "IFRS 9",
                    "url": "https://ifrs.org/9",
                    "snippet": "Financial instruments.",
                    "summary": "IFRS 9 covers classification and impairment.",
                }
            ]
        }
    },
}


def test_searxng_is_asked_for_json_at_its_search_path():
    network = FakeNetwork(
        pages={
            "http://searx.internal:8080/search": reply(
                json.dumps(SEARXNG), content_type="application/json"
            )
        }
    )

    results = Searxng("http://searx.internal:8080", network).search("kimball scd type 2", limit=5)

    sent = network.sent[0]
    assert sent.method == "GET"
    assert sent.url == "http://searx.internal:8080/search?q=kimball+scd+type+2&format=json"
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("SCD type 2", "https://example.org/scd", "History rows."),
        ("B", "https://example.org/b", ""),
    ]


def test_searxng_without_json_output_says_how_to_turn_it_on():
    network = FakeNetwork(pages={"http://searx.internal/search": reply("Forbidden", status=403)})

    with pytest.raises(SearchError) as caught:
        Searxng("http://searx.internal", network).search("x", limit=5)

    assert "format" in str(caught.value) and "json" in str(caught.value)


def test_langsearch_is_called_with_the_bearer_key():
    network = FakeNetwork(
        pages={
            "https://api.langsearch.com/v1/web-search": reply(
                json.dumps(LANGSEARCH), content_type="application/json"
            )
        }
    )

    results = LangSearch("sk-test", network).search("ifrs 9 impairment", limit=3)

    sent = network.sent[0]
    assert sent.method == "POST"
    assert sent.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(sent.body) == {
        "query": "ifrs 9 impairment",
        "count": 3,
        "summary": True,
        "freshness": "noLimit",
    }
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("IFRS 9", "https://ifrs.org/9", "IFRS 9 covers classification and impairment."),
    ]


def test_a_rejected_langsearch_key_is_a_search_error_without_the_key():
    network = FakeNetwork(
        pages={"https://api.langsearch.com/v1/web-search": reply("{}", status=401)}
    )

    with pytest.raises(SearchError) as caught:
        LangSearch("sk-secret", network).search("x", limit=3)

    assert "key" in str(caught.value) and "sk-secret" not in str(caught.value)


def test_an_unreachable_provider_is_a_search_error():
    with pytest.raises(SearchError):
        Searxng("http://searx.internal", FakeNetwork()).search("x", limit=3)


BLOCKED = [["brave", "Suspended: too many requests"], ["duckduckgo", "CAPTCHA"]]


def searx_answering(body: dict) -> Searxng:
    page = reply(json.dumps(body), content_type="application/json")
    network = FakeNetwork(pages={"http://searx.internal/search": page})
    return Searxng("http://searx.internal", network)


def test_no_results_with_blocked_engines_is_a_search_error_naming_them():
    searx = searx_answering({"results": [], "unresponsive_engines": BLOCKED})

    with pytest.raises(SearchError) as caught:
        searx.search("x", limit=3)

    message = str(caught.value)
    assert "brave (Suspended: too many requests)" in message and "duckduckgo (CAPTCHA)" in message


def test_no_results_without_blocked_engines_is_an_empty_result():
    assert searx_answering({"results": [], "unresponsive_engines": []}).search("x", limit=3) == []
    assert searx_answering({"results": []}).search("x", limit=3) == []


def test_results_from_some_engines_are_kept_while_others_fail():
    body = {**SEARXNG, "unresponsive_engines": BLOCKED}

    assert len(searx_answering(body).search("x", limit=5)) == 2


@pytest.mark.parametrize("junk", ["brave", {"a": 1}, [None, 3, []], 7])
def test_a_malformed_unresponsive_list_is_not_a_failure(junk):
    assert searx_answering({"results": [], "unresponsive_engines": junk}).search("x", limit=3) == []


def test_a_bare_engine_name_in_the_list_still_counts_as_blocked():
    searx = searx_answering({"results": [], "unresponsive_engines": [["brave"], "bing", [1, 2]]})

    with pytest.raises(SearchError, match="brave"):
        searx.search("x", limit=3)


@pytest.mark.parametrize(
    ("query", "sent"),
    [
        ("kimball slowly changing dimension", "kimball slowly changing dimension"),
        ("customer status 'GOLD_PLUS' meaning", "customer status meaning"),
        ('loan "Ahmed Al-Harbi" default rate', "loan default rate"),
        ("account 4111111111111111 card bin", "account card bin"),
        ("contact ahmed@bank.example churn", "contact churn"),
        ("iban SA0380000000608010167519 format", "iban format"),
        ("balance 1,250,000.75 threshold", "balance threshold"),
        ("customer 1029384756 segment", "customer segment"),
        ("transactions on 2024-03-15 cutoff", "transactions on cutoff"),
        ("SQL Server 2019 ISO 20022 IFRS 9", "SQL Server 2019 ISO 20022 IFRS 9"),
        ("call +20 100 123 4567 about churn", "call about churn"),
        ("branch 020-100-123-4567 hours", "branch hours"),
        ("IFRS 9 staging", "IFRS 9 staging"),
        ("SQL Server 2019 dialect", "SQL Server 2019 dialect"),
    ],
)
def test_sample_values_are_stripped_from_a_query(query, sent):
    assert strip_values(query) == sent
