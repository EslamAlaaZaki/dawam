"""The admin page's web access settings: on/off, register and test SearXNG and LangSearch,
and pick the provider of normal and of internal-only Workspaces (ADR 0008)."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from tests.web.fakes import FakeNetwork, reply

WEB = "/api/v1/admin/web"
SEARX = "http://searx.internal:8080"
LANGSEARCH = "https://api.langsearch.com/v1/web-search"
RESULTS = {"results": [{"url": "https://example.org", "title": "t", "content": "c"}]}


@pytest.fixture
def network(services) -> FakeNetwork:
    network = FakeNetwork(
        pages={
            f"{SEARX}/search": reply(json.dumps(RESULTS), content_type="application/json"),
            LANGSEARCH: reply("{}", status=401),
        }
    )
    services.web_network = network
    return network


def register_both(client):
    assert client.put(f"{WEB}/providers/searxng", json={"base_url": SEARX}).status_code == 200
    response = client.put(f"{WEB}/providers/langsearch", json={"api_key": "sk-lang-1"})
    assert response.status_code == 200
    return response.json()


def test_web_access_is_off_with_no_providers_at_first(admin_client):
    body = admin_client.get(WEB).json()

    assert body["enabled"] is False
    assert body["providers"] == []
    assert body["normal_provider"] is None and body["internal_provider"] is None


def test_both_providers_can_be_registered_at_once_and_keys_are_never_returned(admin_client, app):
    body = register_both(admin_client)

    providers = {p["kind"]: p for p in body["providers"]}
    assert providers["searxng"]["internal"] is True
    assert providers["searxng"]["base_url"] == SEARX
    assert providers["langsearch"]["internal"] is False
    assert providers["langsearch"]["has_api_key"] is True
    assert "sk-lang-1" not in json.dumps(body)
    with app.state.engine.connect() as conn:
        sealed = conn.scalar(
            sa.text("SELECT secret_encrypted FROM web_search_providers WHERE kind='langsearch'")
        )
    assert sealed and "sk-lang-1" not in sealed


def test_langsearch_keeps_its_key_when_none_is_sent_and_needs_one_at_first(admin_client):
    first = admin_client.put(f"{WEB}/providers/langsearch", json={})
    assert first.status_code == 422 and first.json()["error"]["code"] == "api_key_required"

    admin_client.put(f"{WEB}/providers/langsearch", json={"api_key": "sk-1"})
    again = admin_client.put(f"{WEB}/providers/langsearch", json={})

    assert again.status_code == 200
    assert again.json()["providers"][0]["has_api_key"] is True


@pytest.mark.parametrize(
    "url", ["", "ftp://searx", "http://user:pw@searx", "http://searx/?q=1", "searx.internal"]
)
def test_a_bad_searxng_url_is_refused(admin_client, url):
    response = admin_client.put(f"{WEB}/providers/searxng", json={"base_url": url})

    assert response.status_code == 422


def test_each_provider_is_tested_through_the_faked_port(admin_client, network):
    register_both(admin_client)

    searx = admin_client.post(f"{WEB}/providers/searxng/test").json()
    lang = admin_client.post(f"{WEB}/providers/langsearch/test").json()

    assert searx["test_ok"] is True and searx["test_error"] is None
    assert lang["test_ok"] is False and "API key" in lang["test_error"]
    assert network.sent[0].url.startswith(f"{SEARX}/search?q=")
    assert network.sent[1].headers["Authorization"] == "Bearer sk-lang-1"


def test_searxng_with_all_engines_blocked_fails_its_test_naming_them(admin_client, network):
    register_both(admin_client)
    blocked = {"results": [], "unresponsive_engines": [["brave", "Suspended: too many requests"]]}
    network.pages[f"{SEARX}/search"] = reply(json.dumps(blocked), content_type="application/json")

    searx = admin_client.post(f"{WEB}/providers/searxng/test").json()

    assert searx["test_ok"] is False and "brave" in searx["test_error"]


def test_the_admin_turns_it_on_and_picks_a_provider_for_each_kind_of_workspace(admin_client):
    register_both(admin_client)

    response = admin_client.put(
        WEB,
        json={"enabled": True, "normal_provider": "langsearch", "internal_provider": "searxng"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["enabled"], body["normal_provider"], body["internal_provider"]) == (
        True,
        "langsearch",
        "searxng",
    )


def test_internal_only_workspaces_cannot_be_given_an_external_provider(admin_client):
    register_both(admin_client)

    response = admin_client.put(
        WEB, json={"enabled": True, "normal_provider": None, "internal_provider": "langsearch"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"]["field"] == "internal_provider"


def test_an_unregistered_provider_cannot_be_picked(admin_client):
    response = admin_client.put(WEB, json={"enabled": True, "normal_provider": "searxng"})

    assert response.status_code == 422


def test_removing_a_provider_unpicks_it(admin_client):
    register_both(admin_client)
    admin_client.put(
        WEB, json={"enabled": True, "normal_provider": "searxng", "internal_provider": "searxng"}
    )

    assert admin_client.delete(f"{WEB}/providers/searxng").status_code == 204

    body = admin_client.get(WEB).json()
    assert body["normal_provider"] is None and body["internal_provider"] is None
    assert [p["kind"] for p in body["providers"]] == ["langsearch"]


def test_members_cannot_see_or_change_web_access(signed_in_client):
    assert signed_in_client.get(WEB).status_code == 403
    assert signed_in_client.put(WEB, json={"enabled": True}).status_code == 403
