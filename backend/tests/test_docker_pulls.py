from __future__ import annotations

from typing import Any

import pytest
import requests
from docker.errors import APIError, ImageNotFound
from docker.models.images import ImageCollection

from tests import docker_pulls


def _error(cls: type[APIError], status: int) -> APIError:
    response = requests.Response()
    response.status_code = status
    return cls("pull failed", response=response)


@pytest.fixture
def pulls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Pull outcomes, consumed in order: an exception is raised, anything else returned."""
    outcomes: list[Any] = []

    def fake_pull(self: ImageCollection, *args: Any, **kwargs: Any) -> Any:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(ImageCollection, "pull", fake_pull)
    monkeypatch.setattr(docker_pulls.time, "sleep", lambda _: None)
    docker_pulls.retry_image_pulls()
    return outcomes


@pytest.mark.parametrize("status", [500, 502, 429])
def test_a_pull_failing_on_the_registry_side_is_retried(pulls: list[Any], status: int) -> None:
    pulls.extend([_error(APIError, status), _error(APIError, status), "image"])
    assert ImageCollection.pull(ImageCollection(), "mariadb:11.4") == "image"


def test_a_missing_image_fails_at_once(pulls: list[Any]) -> None:
    pulls.extend([_error(ImageNotFound, 404), "image"])
    with pytest.raises(ImageNotFound):
        ImageCollection.pull(ImageCollection(), "mariadb:no-such-tag")


def test_the_last_registry_error_is_raised_once_the_retries_run_out(pulls: list[Any]) -> None:
    pulls.extend([_error(APIError, 500)] * (len(docker_pulls.BACKOFF_SECONDS) + 1))
    with pytest.raises(APIError):
        ImageCollection.pull(ImageCollection(), "mariadb:11.4")
    assert pulls == []
