"""``GET /api/v1/version``: what is running."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

import dawam

router = APIRouter(tags=["meta"])


class VersionInfo(BaseModel):
    name: str
    version: str


@router.get("/version", operation_id="getVersion")
def get_version() -> VersionInfo:
    return VersionInfo(name="DAWAM", version=dawam.__version__)
