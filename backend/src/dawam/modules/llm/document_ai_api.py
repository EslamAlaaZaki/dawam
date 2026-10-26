"""Document AI settings (24.1): ``GET|PUT /admin/llm/document-ai`` (admins: the
installation's values and the bounds owners override within) and
``GET|PUT /workspaces/{workspace_id}/document-ai`` (members read, owners override).

Handlers only translate HTTP to ``DocumentAiService`` calls; the service validates,
versions and audits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from dawam.modules.auth import CurrentUser, SecurityEventRecorder
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.request_context import client_ip

from .api import LlmAdmin
from .document_ai import (
    KNOBS,
    OWNER_KNOBS,
    Bounds,
    DocumentAiService,
    InstallationDocumentAi,
    Knob,
    WorkspaceDocumentAi,
)

admin_router = APIRouter(prefix="/admin/llm/document-ai", tags=["llm"])
router = APIRouter(prefix="/workspaces/{workspace_id}/document-ai", tags=["llm"])


def document_ai_service(request: Request) -> DocumentAiService:
    state = request.app.state
    clock = state.services.clock
    events = SecurityEventRecorder(state.engine, clock=clock)
    return DocumentAiService(
        state.engine,
        workspaces=WorkspaceService(state.engine, clock=clock, events=events),
        clock=clock,
        events=events,
    )


DocumentAiServiceDep = Annotated[DocumentAiService, Depends(document_ai_service)]

SettingValue = bool | int | float | str


class KnobInfo(BaseModel):
    key: str
    group: Literal["chunking", "indexing", "retrieval", "reranking"]
    kind: Literal["int", "float", "bool", "choice"]
    label: str
    description: str
    owner: bool = Field(description="Workspace owners may override it.")
    reindex: bool = Field(
        description="A change applies to documents indexed from then on: re-index the "
        "Workspace's documents to apply it to the others."
    )
    default: SettingValue = Field(description="The value in code, when an admin sets none.")
    min: float | None = Field(description="Hard lower limit (numbers only).")
    max: float | None = Field(description="Hard upper limit (numbers only).")
    choices: list[str] = Field(description="The allowed values (`choice` only).")


class NumberBounds(BaseModel):
    min: float
    max: float


class BoundsInput(BaseModel):
    min: float | None = Field(default=None, description="Left out: the hard lower limit.")
    max: float | None = Field(default=None, description="Left out: the hard upper limit.")


def _knob(knob: Knob) -> KnobInfo:
    return KnobInfo(
        key=knob.key,
        group=knob.group,
        kind=knob.kind,
        label=knob.label,
        description=knob.description,
        owner=knob.owner,
        reindex=knob.reindex,
        default=knob.default,
        min=knob.low,
        max=knob.high,
        choices=list(knob.choices),
    )


def _bounds(bounds: dict[str, Bounds]) -> dict[str, NumberBounds]:
    return {k: NumberBounds(min=b.min, max=b.max) for k, b in bounds.items()}


class InstallationDocumentAiSettings(BaseModel):
    version: int = Field(description="Send it back with a change; 0: never changed.")
    updated_at: datetime | None
    values: dict[str, SettingValue] = Field(description="Every knob's installation value.")
    bounds: dict[str, NumberBounds] = Field(
        description="For each numeric knob owners may override, the range they may choose in."
    )
    knobs: list[KnobInfo]


class InstallationDocumentAiRequest(BaseModel):
    version: int = Field(description="The version these settings were read at.")
    values: dict[str, SettingValue] = Field(
        default_factory=dict, description="Knobs left out take their default."
    )
    bounds: dict[str, BoundsInput] = Field(
        default_factory=dict, description="Knobs left out are bounded by their hard limits."
    )


def _installation(settings: InstallationDocumentAi) -> InstallationDocumentAiSettings:
    return InstallationDocumentAiSettings(
        version=settings.version,
        updated_at=settings.updated_at,
        values=settings.values,
        bounds=_bounds(settings.bounds),
        knobs=[_knob(k) for k in KNOBS],
    )


@admin_router.get("", operation_id="getDocumentAiSettings")
def get_document_ai_settings(
    _admin: LlmAdmin, service: DocumentAiServiceDep
) -> InstallationDocumentAiSettings:
    """The installation's Document AI settings and the bounds of owners' overrides (admins
    only)."""
    return _installation(service.installation())


@admin_router.put("", operation_id="setDocumentAiSettings")
def set_document_ai_settings(
    body: InstallationDocumentAiRequest,
    admin: LlmAdmin,
    service: DocumentAiServiceDep,
    request: Request,
) -> InstallationDocumentAiSettings:
    """Replace the installation's Document AI settings (admins only; recorded as a
    `document_ai_settings_changed` security event). 409 `settings_changed` when `version`
    is not the current one; 422 `unknown_setting`, `invalid_setting`, `out_of_range` (outside
    the knob's hard limits), `invalid_bounds`, `not_overridable` (bounds for a knob owners
    cannot override) or `out_of_bounds` (a value outside its own bounds)."""
    return _installation(
        service.set_installation(
            admin,
            version=body.version,
            values=body.values,
            bounds={k: b.model_dump(exclude_none=True) for k, b in body.bounds.items()},
            ip=client_ip(request),
        )
    )


class WorkspaceDocumentAiSettings(BaseModel):
    version: int = Field(description="Send it back with a change; 0: never changed.")
    updated_at: datetime | None
    overrides: dict[str, SettingValue] = Field(
        description="The knobs this Workspace overrides; the others inherit."
    )
    inherited: dict[str, SettingValue] = Field(
        description="The installation's value of every knob owners may override."
    )
    effective: dict[str, SettingValue] = Field(
        description="What indexing and search use: overrides clamped to the bounds, else the "
        "installation's values."
    )
    bounds: dict[str, NumberBounds]
    knobs: list[KnobInfo] = Field(description="The knobs owners may override.")


class WorkspaceDocumentAiRequest(BaseModel):
    version: int = Field(description="The version these settings were read at.")
    overrides: dict[str, SettingValue] = Field(
        default_factory=dict, description="Knobs left out inherit the installation's value."
    )


def _workspace(settings: WorkspaceDocumentAi) -> WorkspaceDocumentAiSettings:
    return WorkspaceDocumentAiSettings(
        version=settings.version,
        updated_at=settings.updated_at,
        overrides=settings.overrides,
        inherited=settings.inherited,
        effective=settings.effective.values(),
        bounds=_bounds(settings.bounds),
        knobs=[_knob(k) for k in OWNER_KNOBS],
    )


@router.get("", operation_id="getWorkspaceDocumentAi")
def get_workspace_document_ai(
    workspace_id: uuid.UUID, user: CurrentUser, service: DocumentAiServiceDep
) -> WorkspaceDocumentAiSettings:
    """The Workspace's Document AI overrides and effective settings (any member)."""
    return _workspace(service.get(user, workspace_id))


@router.put("", operation_id="setWorkspaceDocumentAi")
def set_workspace_document_ai(
    workspace_id: uuid.UUID,
    body: WorkspaceDocumentAiRequest,
    user: CurrentUser,
    service: DocumentAiServiceDep,
) -> WorkspaceDocumentAiSettings:
    """Replace the Workspace's overrides (owners of an active Workspace; audited). 409
    `settings_changed` when `version` is not the current one; 422 `not_overridable` for an
    admin-only knob, `out_of_bounds` outside the installation's bounds, and the value errors
    of the installation settings."""
    return _workspace(
        service.set_overrides(user, workspace_id, version=body.version, overrides=body.overrides)
    )
