"""``client_ip``: the address a request came from, as uvicorn reports it."""

from starlette.requests import Request

from dawam.platform.request_context import client_ip


def request_from(client) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": client})


def test_the_client_address_is_the_requests_peer():
    assert client_ip(request_from(("203.0.113.7", 51234))) == "203.0.113.7"


def test_a_request_without_a_peer_has_no_address():
    assert client_ip(request_from(None)) is None
