"""The locked-down network around Crawl4AI (ADR 0008, #545), read from the Compose files and the
filter's settings. Nothing is started: running it is the by-hand check in deploy/README.md."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

import pytest
import yaml

from dawam.modules.web import is_public_address

REPO = Path(__file__).resolve().parents[3]
CRAWL4AI_DIR = REPO / "deploy" / "crawl4ai"
FILTER_CONF = CRAWL4AI_DIR / "egress-filter.conf"
LOCKED_DOWN = ("crawl4ai", "egress-filter", "crawl4ai-dns")
INTERNAL = "crawl4ai-internal"
EGRESS = "crawl4ai-egress"
FILTER_ADDRESS = "172.30.127.10"
DNS_ADDRESS = "172.30.127.11"
PINNED = re.compile(r"^[^:@]+(/[^:@]+)*:[^:@]+@sha256:[0-9a-f]{64}$")


@pytest.fixture(params=["compose.yaml", "deploy/docker-compose.yaml"])
def compose(request) -> dict:
    return yaml.safe_load((REPO / request.param).read_text(encoding="utf-8"))


def networks_of(service: dict) -> set[str]:
    return set(service.get("networks") or ["default"])


def test_crawl4ai_is_only_on_the_internal_network_never_the_default_one(compose):
    assert networks_of(compose["services"]["crawl4ai"]) == {INTERNAL}
    assert compose["networks"][INTERNAL]["internal"] is True


def test_every_locked_down_service_is_behind_the_profile_with_a_pinned_image_and_no_host_port(
    compose,
):
    for name in LOCKED_DOWN:
        service = compose["services"][name]
        assert service["profiles"] == ["crawl4ai"], name
        assert PINNED.match(service["image"]), name
        assert "ports" not in service, name
        assert "expose" not in service, name
        assert service["deploy"]["resources"]["limits"]["memory"], name
        assert "env_file" not in service, name  # none of DAWAM's secrets


def test_crawl4ai_has_a_2_gb_limit_at_most_2_pages_and_a_health_check(compose):
    service = compose["services"]["crawl4ai"]

    assert service["deploy"]["resources"]["limits"]["memory"] == "2g"
    assert "healthcheck" in service
    settings = yaml.safe_load((CRAWL4AI_DIR / "config.yml").read_text(encoding="utf-8"))
    assert settings["crawler"]["pool"]["max_pages"] == 2


def test_the_only_services_with_a_way_out_are_the_filter_and_its_resolver(compose):
    on_egress = {n for n, s in compose["services"].items() if EGRESS in networks_of(s)}
    assert on_egress == {"egress-filter", "crawl4ai-dns"}
    assert not compose["networks"][EGRESS].get("internal")
    for name in ("egress-filter", "crawl4ai-dns"):
        assert networks_of(compose["services"][name]) == {INTERNAL, EGRESS}


def test_only_the_app_shares_the_internal_network_with_crawl4ai(compose):
    on_internal = {n for n, s in compose["services"].items() if INTERNAL in networks_of(s)}
    assert on_internal == {"app", *LOCKED_DOWN}
    assert networks_of(compose["services"]["app"]) == {"default", INTERNAL}


def test_the_app_stays_on_the_default_network_and_publishes_nothing_more(compose):
    app = compose["services"]["app"]
    assert "ports" not in app
    assert "expose" not in app


def test_crawl4ai_leaves_only_through_the_filter_and_resolves_only_through_its_sidecar(compose):
    services = compose["services"]
    crawl4ai = services["crawl4ai"]
    assert crawl4ai["environment"]["CRAWL4AI_UPSTREAM_PROXY"] == f"http://{FILTER_ADDRESS}:3128"
    assert services["egress-filter"]["networks"][INTERNAL]["ipv4_address"] == FILTER_ADDRESS
    assert services["crawl4ai-dns"]["networks"][INTERNAL]["ipv4_address"] == DNS_ADDRESS
    assert any(v.endswith(":/etc/resolv.conf:ro") for v in crawl4ai["volumes"])
    resolv = (CRAWL4AI_DIR / "resolv.conf").read_text(encoding="utf-8")
    assert f"nameserver {DNS_ADDRESS}" in resolv


def test_nothing_turns_off_crawl4ais_own_checks_or_gives_it_more_power(compose):
    environment = compose["services"]["crawl4ai"]["environment"]
    assert set(environment) == {"CRAWL4AI_API_TOKEN", "CRAWL4AI_UPSTREAM_PROXY"}


def test_crawl4ai_runs_with_least_privilege(compose):
    service = compose["services"]["crawl4ai"]
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["read_only"] is True


def test_the_settings_keep_crawl4ais_default_identity_and_the_full_browser():
    settings = yaml.safe_load((CRAWL4AI_DIR / "config.yml").read_text(encoding="utf-8"))

    assert settings["crawler"]["base_config"] == {}
    assert settings["crawler"]["browser"] == {"kwargs": {}, "extra_args": []}
    assert settings["webhooks"]["enabled"] is False


# -- the filter's settings -----------------------------------------------------------------


def filter_lines() -> list[str]:
    lines = FILTER_CONF.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def refused_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [
        ipaddress.ip_network(line.split()[-1])
        for line in filter_lines()
        if line.startswith("acl refused_address dst ")
    ]


def refused(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    # Squid holds an IPv4-mapped address as the IPv4 address itself.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in refused_networks())


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.0.0.7",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "172.30.126.2",  # the database's network
        "172.30.127.128",  # this network
        "192.0.0.8",
        "192.0.2.1",
        "192.168.1.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "239.255.255.250",
        "240.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::10.0.0.1",
        "64:ff9b::a00:1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
        "2001:db8::1",
        "2002:a00:1::",  # 6to4
        "fc00::1",
        "fd12:3456:789a::1",
        "fe80::1",
        "ff02::1",
    ],
)
def test_the_filter_refuses_every_internal_range_dawams_own_check_refuses(address):
    assert not is_public_address(address)  # the two lists mean the same
    assert refused(address)


@pytest.mark.parametrize("address", ["1.1.1.1", "93.184.215.14", "2606:4700:4700::1111"])
def test_the_filter_leaves_public_addresses_alone(address):
    assert is_public_address(address)
    assert not refused(address)


def test_the_filter_does_not_list_ipv4_mapped_which_would_refuse_every_ipv4_address():
    mapped = ipaddress.ip_network("::ffff:0:0/96")
    assert not any(
        net.version == 6 and (net.subnet_of(mapped) or mapped.subnet_of(net))
        for net in refused_networks()
    )


def test_the_refusals_come_before_the_only_allow_rule():
    rules = [line for line in filter_lines() if line.startswith("http_access ")]

    assert rules == [
        "http_access deny !crawl4ai_network",
        "http_access deny refused_address",
        "http_access deny !web_port",
        "http_access deny CONNECT !SSL_ports",
        "http_access allow crawl4ai_network",
    ]


def test_the_filter_only_reaches_web_ports_and_only_serves_the_crawl4ai_network():
    lines = filter_lines()

    assert "acl web_port port 80 443" in lines
    assert "acl crawl4ai_network src 172.30.127.0/24" in lines


def test_a_dns_answer_outlives_the_check_so_the_connection_uses_the_same_one():
    lines = filter_lines()

    assert "negative_dns_ttl 5 minutes" in lines  # also the floor of a positive answer's time
    assert "positive_dns_ttl 5 minutes" in lines
    assert "hosts_file none" in lines


def test_the_filter_keeps_nothing_and_says_nothing_about_who_asked():
    lines = filter_lines()

    assert "cache deny all" in lines
    assert "forwarded_for delete" in lines
    assert "via off" in lines


def test_the_ci_never_starts_it():
    workflows = REPO / ".github" / "workflows"
    for workflow in workflows.glob("*.y*ml"):
        assert "--profile crawl4ai" not in workflow.read_text(encoding="utf-8"), workflow.name
