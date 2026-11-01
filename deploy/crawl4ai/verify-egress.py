"""The by-hand check of the locked-down network (deploy/README.md, ADR 0008, #545). Never run by
CI: it needs the `crawl4ai` profile running and reaches real sites. From the repository root:

    docker compose exec -T app python - <PROBE_PAGE_URL> < deploy/crawl4ai/verify-egress.py

<PROBE_PAGE_URL> is egress-probe.html on any public address. Prints PASS or FAIL per check.
"""

import http.client
import json
import os
import sys
import urllib.error
import urllib.request

FILTER = ("172.30.127.10", 3128)
SERVICE = os.environ.get("DAWAM_CRAWL4AI_URL", "http://crawl4ai:11235").rstrip("/")
TOKEN = os.environ["DAWAM_CRAWL4AI_API_TOKEN"]
PUBLIC_PAGE = "https://example.com/"
# What the filter says instead of connecting: refused, or a name it cannot resolve.
NO_CONNECTION = {403, 503, 504}
INTERNAL_HTTP = [
    "http://db:5432/",
    "http://db/",
    "http://app:8000/healthz",
    "http://crawl4ai:11235/health",
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "http://172.16.0.1/",
    "http://172.30.126.10/",
    "http://192.168.1.1/",
    "http://100.64.0.1/",
    "http://224.0.0.1/",
    "http://[::1]/",
    "http://[::ffff:169.254.169.254]/",
    "http://[fd00::1]/",
    # A public name with a private answer: what DNS rebinding comes down to.
    "http://169.254.169.254.nip.io/",
    "http://10.0.0.1.nip.io/",
]
INTERNAL_CONNECT = ["169.254.169.254:443", "10.0.0.1:443", "10.0.0.1.nip.io:443", "[::1]:443"]
WRONG_PORT = ["1.1.1.1:22", "1.1.1.1:25", "1.1.1.1:8443"]
failures = 0


def report(ok, what, detail=""):
    global failures
    failures += not ok
    print(("PASS" if ok else "FAIL"), what, detail)


def through_filter(method, target):
    connection = http.client.HTTPConnection(*FILTER, timeout=20)
    try:
        connection.request(method, target)
        return connection.getresponse().status
    finally:
        connection.close()


def crawl(url, **params):
    body = {
        "urls": [url],
        "browser_config": {},
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {"wait_until": "domcontentloaded", "delay_before_return_html": 4.0, **params},
        },
    }
    request = urllib.request.Request(
        f"{SERVICE}/crawl",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return json.load(reply)["results"][0]
    except urllib.error.HTTPError as error:
        return {"success": False, "status_code": error.code}


print("-- the filter, asked directly")
for target in INTERNAL_HTTP:
    status = through_filter("GET", target)
    report(status in NO_CONNECTION, f"GET {target}", status)
for target in INTERNAL_CONNECT:
    status = through_filter("CONNECT", target)
    report(status in NO_CONNECTION, f"CONNECT {target}", status)
for target in WRONG_PORT:
    status = through_filter("CONNECT", target)
    report(status in NO_CONNECTION, f"CONNECT {target} (not a web port)", status)
status = through_filter("GET", "http://example.com/")
report(status == 200, "GET http://example.com/ (public)", status)
status = through_filter("CONNECT", "example.com:443")
report(status == 200, "CONNECT example.com:443 (public)", status)

print("-- Crawl4AI, asked for internal addresses")
for target in ("http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://db:5432/"):
    result = crawl(target)
    report(result.get("success") is not True, f"crawl {target}", result.get("status_code"))

print("-- the browser, on a public page")
result = crawl(PUBLIC_PAGE)
words = ((result.get("markdown") or {}).get("raw_markdown") or "")[:40]
report(result.get("success") is True and bool(words), f"crawl {PUBLIC_PAGE}", repr(words))

if len(sys.argv) > 1:
    print("-- the browser, on a page that asks for internal addresses")
    result = crawl(sys.argv[1], capture_network_requests=True)
    report(result.get("success") is True, f"crawl {sys.argv[1]}", result.get("status_code"))
    events = result.get("network_requests") or []
    answers = [e for e in events if e.get("event_type") == "response"]
    probes = [e for e in answers if e.get("url") != sys.argv[1]]
    for event in probes:  # a refusal comes from the proxy; any other status came from the target
        report(event.get("status") in NO_CONNECTION, f"page asked {event['url']}", event["status"])
    for event in events:
        if event.get("event_type") == "request_failed":
            report(True, f"page asked {event.get('url')}", event.get("failure_text"))
    report(bool(events), "the browser did run the page's requests", f"{len(events)} events")
else:
    print("(no probe page given: the browser part was skipped)")

print("FAILED" if failures else "ALL PASSED", failures)
sys.exit(1 if failures else 0)
