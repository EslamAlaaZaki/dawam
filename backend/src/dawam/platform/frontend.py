"""Serving the built single-page frontend (``DAWAM_FRONTEND_DIST``) at ``/``.

Files in the directory are served as they are. Any other ``GET``/``HEAD`` that is
neither under ``/api`` nor names a file (its last path segment has no ``.``) is a
client-side route, so it gets ``index.html`` and the frontend's router takes over;
that is what makes deep links such as ``/login`` work. Unknown API paths and missing
files stay 404s in the standard error shape.
"""

from __future__ import annotations

import stat
from pathlib import Path

import anyio.to_thread
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_INDEX = "index.html"


def _is_client_route(scope: Scope) -> bool:
    path: str = scope["path"]
    if path == "/api" or path.startswith("/api/"):
        return False
    last_segment = path.rstrip("/").rsplit("/", 1)[-1]
    return "." not in last_segment


class SinglePageApp(StaticFiles):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory, html=True)

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or not _is_client_route(scope):
                raise
        full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, _INDEX)
        if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
            raise HTTPException(status_code=404)
        return self.file_response(full_path, stat_result, scope)
