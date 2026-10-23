"""Retries Docker image pulls that fail for reasons on the registry's side.

Testcontainers pulls every image it lacks through ``docker.models.images.ImageCollection
.pull``. Docker Hub sometimes answers a pull with a 5xx or a 429 (rate limit), which fails
every test that uses the container although nothing is wrong with the code. Wrapping that
one method retries such pulls with a backoff; other errors (unknown image, bad tag) still
fail at once.
"""

from __future__ import annotations

import functools
import time
from typing import Any

from docker.errors import APIError
from docker.models.images import ImageCollection

BACKOFF_SECONDS = (5, 15, 30, 60)
"""Waits before each retry: about two minutes in all before giving up."""


def _is_transient(error: APIError) -> bool:
    return error.is_server_error() or error.status_code == 429


def retry_image_pulls() -> None:
    original = ImageCollection.pull
    if getattr(original, "retries_transient_errors", False):
        return

    @functools.wraps(original)
    def pull(self: ImageCollection, *args: Any, **kwargs: Any) -> Any:
        for wait in BACKOFF_SECONDS:
            try:
                return original(self, *args, **kwargs)
            except APIError as error:
                if not _is_transient(error):
                    raise
                time.sleep(wait)
        return original(self, *args, **kwargs)

    pull.retries_transient_errors = True  # type: ignore[attr-defined]
    ImageCollection.pull = pull  # type: ignore[method-assign]
