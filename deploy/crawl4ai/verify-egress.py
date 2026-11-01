"""The by-hand check of the locked-down network (deploy/README.md, ADR 0008, #545). Never run by
CI: it needs the `crawl4ai` profile running and reaches real sites. From the repository root:

    docker compose exec -T app python - http://<public-ip>/egress-probe.html \\
      < deploy/crawl4ai/verify-egress.py

The probe page must be on plain http: an https page cannot request http:// targets at all (mixed
content), so it would prove nothing. Serve it from a public IP address: a plain http request
reaches its site with the IP address as `Host`, which a shared tunnel cannot route.

It fails closed. A check passes only on evidence of a refusal by the filter or by Crawl4AI's own
proxy; an error, a missing event or anything unexpected is a FAIL.
"""

import base64
import http.client
import http.server
import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

FILTER = ("172.30.127.10", 3128)
PUBLIC_PAGE = "https://example.com/"
TUNNEL_FAILED = "net::ERR_TUNNEL_CONNECTION_FAILED"
DENIED = "ERR_ACCESS_DENIED"  # any other Squid error means it tried, or could not tell
CRAWL4AI_REFUSAL = "URL blocked"  # the body of a refusal by Crawl4AI's own proxy
CANARY = "dawam-egress-canary-7f3a"
REDIRECTED = ["http://169.254.169.254/", "http://10.0.0.1/"]
# The one list of what a page may never reach. Chromium can bypass a proxy for loopback, so those
# are asked for by each name, on the server's own port and on its Redis.
PAGE_TARGETS = [
    "http://db:5432/",
    "http://db/",
    "http://app:8000/healthz",
    "http://crawl4ai:11235/health",
    "http://localhost:11235/health",
    "http://127.0.0.1:11235/health",
    "http://[::1]:11235/health",
    "http://localhost:6379/",
    "http://127.0.0.1:6379/",
    "http://[::1]:6379/",
    "http://169.254.169.254/latest/meta-data/",
    "https://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "https://10.0.0.1/",
    "http://192.168.1.1/",
    "http://172.30.126.10:8080/",
    "http://172.30.127.10:3128/",
    "http://10.0.0.1.nip.io/",  # a static answer, not rebinding: see rebinding_checks
]
# Target -> the filter's acceptable errors. `http://db/` is the one exception, here only: the
# filter is on no network with `db`, so failing to resolve it is what proves that isolation,
# and port 80 passes the ACL. A page never gets this far: Crawl4AI's proxy cannot resolve `db`
# either, so it answers its own `URL blocked`, and ERR_DNS_FAIL on a page is a FAIL.
FILTER_GET = {
    "http://db:5432/": {"ERR_ACCESS_DENIED"},
    "http://db/": {"ERR_DNS_FAIL"},
    "http://app:8000/healthz": {"ERR_ACCESS_DENIED"},
    "http://crawl4ai:11235/health": {"ERR_ACCESS_DENIED"},
    "http://127.0.0.1/": {"ERR_ACCESS_DENIED"},
    "http://169.254.169.254/latest/meta-data/": {"ERR_ACCESS_DENIED"},
    "http://10.0.0.1/": {"ERR_ACCESS_DENIED"},
    "http://172.16.0.1/": {"ERR_ACCESS_DENIED"},
    "http://172.30.126.10/": {"ERR_ACCESS_DENIED"},
    "http://192.168.1.1/": {"ERR_ACCESS_DENIED"},
    "http://100.64.0.1/": {"ERR_ACCESS_DENIED"},
    "http://224.0.0.1/": {"ERR_ACCESS_DENIED"},
    "http://[::1]/": {"ERR_ACCESS_DENIED"},
    "http://[::ffff:169.254.169.254]/": {"ERR_ACCESS_DENIED"},
    "http://[fd00::1]/": {"ERR_ACCESS_DENIED"},
    "http://169.254.169.254.nip.io/": {"ERR_ACCESS_DENIED"},
    "http://10.0.0.1.nip.io/": {"ERR_ACCESS_DENIED"},
}
FILTER_CONNECT = [
    "169.254.169.254:443",
    "10.0.0.1:443",
    "10.0.0.1.nip.io:443",
    "[::1]:443",
    "1.1.1.1:22",  # the next three: public, but not a web port
    "1.1.1.1:25",
    "1.1.1.1:8443",
]
PUBLIC_RESOLVERS = [
    "1.1.1.1",
    "1.0.0.1",
    "8.8.8.8",
    "8.8.4.4",
    "9.9.9.9",
    "149.112.112.112",
    "208.67.222.222",
    "208.67.220.220",
]
failures = 0


def report(ok, what, detail=""):
    global failures
    failures += not ok
    print("PASS" if ok else "FAIL", what, detail)


def squid_error(headers) -> str | None:
    """Squid's own name for a refusal, from a response's headers (any case of the name)."""
    for name, value in dict(headers).items():
        if name.lower() == "x-squid-error":
            return str(value).split()[0] if value else None
    return None


def judge_event(events: list, target: str, ask_filter=None) -> tuple[bool, str]:
    """Did the page's request for ``target`` end in a refusal? Exactly one final event, and only
    a refusal's own marks pass: a 403 alone could be the internal service answering, and another
    Squid error (ERR_CONNECT_FAIL, ERR_READ_TIMEOUT) means it tried. A failed tunnel looks the same
    whether refused or unreachable, so it is not proven until ``ask_filter(target)``, the filter
    asked directly for that address, gives ERR_ACCESS_DENIED."""
    final = [
        e
        for e in events
        if e.get("url") == target
        and e.get("event_type")
        in ("response", "request_failed", "response_capture_error", "request_failed_capture_error")
    ]
    if len(final) != 1:
        return False, f"{len(final)} final events, expected exactly 1"
    event = final[0]
    if event["event_type"] == "request_failed":
        text = event.get("failure_text") or ""
        if text != TUNNEL_FAILED:
            return False, text
        error = ask_filter(target) if ask_filter else None
        return error == DENIED, f"{text}; the filter, asked directly: {error}"
    if event["event_type"] != "response":
        return False, event["event_type"]
    status = event.get("status")
    body = ((event.get("body") or {}).get("text") or "").strip()
    error = squid_error(event.get("headers") or {})
    if error == DENIED or (DENIED in body and "(squid" in body.lower()):
        return True, f"{status}, refused by the filter"
    if status == 403 and body == CRAWL4AI_REFUSAL:
        return True, f"{status}, refused by Crawl4AI's proxy"
    return False, f"{status} {error}, not a refusal: {body[:80]!r}"


def judge_refused_crawl(status: int, answer: dict) -> tuple[bool, str]:
    """A crawl of an internal address: the service answered, and said why it did not read it."""
    if status == 400:
        detail = str(answer.get("detail") or "")
        return detail.startswith(CRAWL4AI_REFUSAL), detail[:80]
    if status != 200:
        return False, f"the service answered HTTP {status}"
    error = str(answer.get("error_message") or "")
    named = CRAWL4AI_REFUSAL in error or "ERR_TUNNEL_CONNECTION_FAILED" in error
    return answer.get("success") is not True and named, error[:80]


def judge_rebinding_crawl(status: int, answer: dict) -> tuple[bool, str]:
    """A crawl of a rebinding name. It passes as a page read without the canary (the public
    answer) or as a named refusal (the private answer). Anything else proves nothing: FAIL."""
    html = answer.get("html")
    if CANARY in str(html):
        return False, "the canary answered: the browser reached this container"
    if status == 200 and answer.get("success") is True and html:
        return True, "read the public answer"
    refused, detail = judge_refused_crawl(status, answer)
    return refused, (f"refused: {detail}" if refused else f"inconclusive: {status} {detail}")


def filter_says(target):
    """The filter's error when asked directly for ``target``, by the route a browser would use."""
    parts = urllib.parse.urlsplit(target)
    if parts.scheme == "https":
        return through_filter("CONNECT", f"{parts.netloc}:443")[1]
    return through_filter("GET", target)[1]


def through_filter(method, target):
    """The filter's status, its X-Squid-Error and the start of the body."""
    connection = http.client.HTTPConnection(*FILTER, timeout=25)
    try:
        connection.request(method, target)
        reply = connection.getresponse()
        body = b"" if method == "CONNECT" else reply.read(65536)
        return reply.status, squid_error(reply.getheaders()), body.decode("utf-8", "replace")
    finally:
        connection.close()


def crawl(service, token, url, **params):
    """The service's HTTP status, and its first result or its error."""
    body = {
        "urls": [url],
        "browser_config": {},
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "wait_until": "domcontentloaded",
                "delay_before_return_html": 8.0,
                "capture_network_requests": True,
                **params,
            },
        },
    }
    request = urllib.request.Request(
        f"{service}/crawl",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as reply:
            return reply.status, json.load(reply)["results"][0]
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except ValueError:
            return error.code, {}
    except (OSError, ValueError, KeyError, IndexError) as error:
        return 0, {"detail": type(error).__name__}


def filter_checks():
    print("-- the filter, asked directly")
    for target, expected in FILTER_GET.items():
        status, error, _ = through_filter("GET", target)
        report(error in expected, f"GET {target}", f"{status} {error}")
    for target in FILTER_CONNECT:
        status, error, _ = through_filter("CONNECT", target)
        report(error == "ERR_ACCESS_DENIED", f"CONNECT {target}", f"{status} {error}")
    status, error, _ = through_filter("GET", "http://example.com/")
    report(status == 200 and not error, "GET http://example.com/ (public)", f"{status} {error}")
    status, error, _ = through_filter("CONNECT", "example.com:443")
    report(status == 200 and not error, "CONNECT example.com:443 (public)", f"{status} {error}")


def crawl_checks(service, token):
    print("-- Crawl4AI, asked for internal addresses")
    for target in ("http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/", "http://db/"):
        ok, detail = judge_refused_crawl(*crawl(service, token, target))
        report(ok, f"crawl {target}", detail)


def page_checks(service, token, probe):
    print("-- the browser, on a page that asks for internal addresses")
    if not probe.startswith("http://"):
        report(False, "the probe page must be on plain http", probe)
        return
    targets = [PUBLIC_PAGE, *PAGE_TARGETS]
    query = base64.b64encode(json.dumps(targets).encode()).decode()
    url = f"{probe}?{urllib.parse.urlencode({'targets': query})}"
    status, result = crawl(service, token, url)
    report(status == 200 and result.get("success") is True, "crawl the probe page", status)
    events = result.get("network_requests") or []
    control = [
        e for e in events if e.get("url") == PUBLIC_PAGE and e.get("event_type") == "response"
    ]
    alive = len(control) == 1 and control[0].get("status") == 200
    report(alive, f"positive control: the page reached {PUBLIC_PAGE}", len(control))
    if not alive:
        report(False, "the negatives prove nothing without the positive control")
        return
    for target in PAGE_TARGETS:
        ok, detail = judge_event(events, target, filter_says)
        report(ok, f"page asked {target}", detail)

    print("-- the browser, redirected to internal addresses")
    for target in REDIRECTED:
        moved = urllib.parse.urlencode({"url": target, "status_code": 302})
        status, result = crawl(service, token, f"https://httpbin.org/redirect-to?{moved}")
        ok, detail = judge_event(result.get("network_requests") or [], target, filter_says)
        report(status == 200 and ok, f"302 to {target}", f"{status} {detail}")
        moved = urllib.parse.urlencode({"location": target})
        status, result = crawl(service, token, f"{probe}?{moved}")
        ok, detail = judge_event(result.get("network_requests") or [], target, filter_says)
        report(status == 200 and ok, f"location= to {target}", f"{status} {detail}")


class Canary(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = CANARY.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def hex_of(address: str) -> str:
    return socket.inet_aton(address).hex()


def rebinding_checks(service, token):
    """Real rebinding, not a static name: rbndr.us answers with one of the two addresses in the
    name, at random and with a TTL of about a second. One of them is this container, listening
    on port 80, so a connection that got through would return the canary. A fresh public address
    per try gives a fresh name, outside the filter's DNS cache."""
    print("-- DNS rebinding, through rbndr.us (an outside service)")
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", 80), Canary)
    except OSError as error:
        report(False, "listening on port 80: run with `exec -u root` (deploy/README.md)", error)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    probe = socket.create_connection(FILTER, timeout=10)
    own = probe.getsockname()[0]
    probe.close()
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        "http://127.0.0.1/", timeout=5
    ) as reply:
        report(reply.read().decode() == CANARY, "the canary answers here", own)
    status, error, body = through_filter("GET", f"http://{own}/")
    report(CANARY not in body and error == "ERR_ACCESS_DENIED", f"GET http://{own}/", error)

    refused = other = 0
    for public in PUBLIC_RESOLVERS:
        name = f"{hex_of(public)}.{hex_of(own)}.rbndr.us"
        status, error, body = through_filter("GET", f"http://{name}/")
        reached = CANARY in body
        refused += error == "ERR_ACCESS_DENIED"
        other += not reached and error != "ERR_ACCESS_DENIED" and error != "ERR_DNS_FAIL"
        report(not reached, f"filter, GET http://{name}/", f"{status} {error}")
    report(
        refused > 0 and other > 0,
        "the filter saw both answers (run again if not: else this proved nothing)",
        f"private answers refused: {refused}, public answers: {other}",
    )
    for public in PUBLIC_RESOLVERS[:4]:
        name = f"{hex_of(own)}.{hex_of(public)}.rbndr.us"
        status, result = crawl(service, token, f"http://{name}/", capture_network_requests=False)
        ok, detail = judge_rebinding_crawl(status, result)
        report(ok, f"browser, http://{name}/", detail)
    server.shutdown()


def main():
    service = os.environ.get("DAWAM_CRAWL4AI_URL", "http://crawl4ai:11235").rstrip("/")
    token = os.environ.get("DAWAM_CRAWL4AI_API_TOKEN", "")
    print("-- the service")
    status, result = crawl(service, token, PUBLIC_PAGE)
    words = ((result.get("markdown") or {}).get("raw_markdown") or "")[:40]
    report(status == 200 and result.get("success") is True and words, PUBLIC_PAGE, status)
    if status != 200:
        report(False, "the service never answered 200: the checks below would prove nothing")
        print("FAILED", failures)
        sys.exit(1)
    filter_checks()
    crawl_checks(service, token)
    if len(sys.argv) > 1:
        page_checks(service, token, sys.argv[1])
    else:
        report(False, "no probe page given: the browser part did not run")
    rebinding_checks(service, token)
    print("FAILED" if failures else "ALL PASSED", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
