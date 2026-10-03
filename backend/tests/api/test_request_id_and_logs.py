"""Every request has a request id, returned in X-Request-ID and on every log line."""

import json
import logging
import re

import pytest
from fastapi import APIRouter, FastAPI

from dawam.platform.logs import JsonFormatter


@pytest.fixture(autouse=True)
def probe_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/_probe")

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    @router.get("/log")
    def log_something() -> dict[str, str]:
        logging.getLogger("dawam.test").info("inside handler", extra={"answer": 42})
        return {}

    app.include_router(router)


@pytest.fixture(autouse=True)
def info_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO)
    return caplog


def records(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


def as_json(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_a_given_request_id_is_returned(anonymous_client):
    response = anonymous_client.get("/api/v1/version", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_a_request_id_is_generated_when_none_is_given(anonymous_client):
    first = anonymous_client.get("/api/v1/version").headers["X-Request-ID"]
    second = anonymous_client.get("/api/v1/version").headers["X-Request-ID"]

    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert first != second


def test_an_unsafe_request_id_is_replaced(anonymous_client):
    response = anonymous_client.get("/healthz", headers={"X-Request-ID": "bad id\twith junk"})

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_errors_carry_the_request_id_too(anonymous_client):
    not_found = anonymous_client.get("/api/v1/nope", headers={"X-Request-ID": "r-404"})
    crashed = anonymous_client.get("/api/v1/_probe/boom", headers={"X-Request-ID": "r-500"})

    assert not_found.headers["X-Request-ID"] == "r-404"
    assert crashed.status_code == 500
    assert crashed.headers["X-Request-ID"] == "r-500"


def test_each_request_is_logged_as_json_with_its_request_id(anonymous_client, info_logs):
    anonymous_client.get("/api/v1/version", headers={"X-Request-ID": "r-access"})

    [record] = [r for r in records(info_logs, "request completed") if r.path == "/api/v1/version"]
    line = as_json(record)
    assert line["message"] == "request completed"
    assert line["level"] == "INFO"
    assert line["logger"] == "dawam.request"
    assert line["request_id"] == "r-access"
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)
    assert "timestamp" in line


def test_application_logs_get_the_request_id_and_extras(anonymous_client, info_logs):
    anonymous_client.get("/api/v1/_probe/log", headers={"X-Request-ID": "r-app"})

    [record] = records(info_logs, "inside handler")
    line = as_json(record)
    assert line["request_id"] == "r-app"
    assert line["answer"] == 42


def test_unhandled_errors_are_logged_with_traceback_and_request_id(anonymous_client, info_logs):
    anonymous_client.get("/api/v1/_probe/boom", headers={"X-Request-ID": "r-boom"})

    [record] = records(info_logs, "unhandled error")
    line = as_json(record)
    assert line["level"] == "ERROR"
    assert line["request_id"] == "r-boom"
    assert "RuntimeError: kaboom" in line["exc_info"]


def test_logs_outside_a_request_have_no_request_id():
    record = logging.LogRecord("dawam.test", logging.INFO, __file__, 1, "idle", None, None)

    assert "request_id" not in as_json(record)
