"""Taking a model out of use, by removing it or unticking one of its roles (spec §6.18,
ADR 0005). One rule for both: a Workspace needs a replacement for a role only when the model
is its default for the role or its only model for it; the admin picks one per Workspace and
role (an internal-only Workspace takes only an internal model), applied in one transaction
as the Workspace's new default. Workspaces holding it as a non-default extra just lose it, and
so do Workspaces holding it for an optional role (vision).
A Workspace's indexed documents pin its embedding model, so that role cannot be released
while any exist; nothing is ever re-indexed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.errors import ApiError

from .tables import ModelRecord, WorkspaceModelRecord
from .workspace_ai import load_settings, model_is_internal

ROLES = ("agent", "light", "embedding", "vision")
OPTIONAL_ROLES = frozenset({"vision"})
"""Roles a Workspace may go without: losing the model needs no replacement (without a vision
model, scanned pages are read by the self-hosted OCR engine)."""


@dataclass(frozen=True)
class WorkspaceRef:
    id: uuid.UUID
    name: str
    roles: list[str] = field(default_factory=list)
    """The roles needing a replacement in this Workspace."""
    internal_only: bool = False


@dataclass(frozen=True)
class ModelUsage:
    workspaces: list[WorkspaceRef]
    """Workspaces that need a replacement: the model is a default or the only one."""
    indexed_workspaces: list[WorkspaceRef]
    """Workspaces with documents indexed by this (embedding) model."""

    @property
    def roles(self) -> list[str]:
        used: set[str] = set()
        for workspace in self.workspaces:
            used.update(workspace.roles)
        return [r for r in ROLES if r in used]

    @property
    def in_use(self) -> bool:
        return bool(self.workspaces or self.indexed_workspaces)


@dataclass(frozen=True)
class WorkspaceReplacement:
    workspace_id: uuid.UUID
    agent: uuid.UUID | None = None
    light: uuid.UUID | None = None
    embedding: uuid.UUID | None = None


def _not_found() -> ApiError:
    return ApiError(404, "model_not_found", "That model does not exist.")


def _names(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = db.execute(sa.text("SELECT id, name FROM workspaces WHERE id = ANY(:ids)"), {"ids": ids})
    return {row.id: row.name for row in rows}


def _needs_replacement(db: Session, row: WorkspaceModelRecord) -> bool:
    if row.role in OPTIONAL_ROLES:
        return False
    if row.is_default:
        return True
    others = db.scalar(
        sa.select(sa.func.count())
        .select_from(WorkspaceModelRecord)
        .where(
            WorkspaceModelRecord.workspace_id == row.workspace_id,
            WorkspaceModelRecord.role == row.role,
            WorkspaceModelRecord.model_id != row.model_id,
        )
    )
    return not others


def _usage(db: Session, model_id: uuid.UUID, only: tuple[str, ...] = ROLES) -> ModelUsage:
    rows = db.scalars(
        sa.select(WorkspaceModelRecord).where(
            WorkspaceModelRecord.model_id == model_id, WorkspaceModelRecord.role.in_(only)
        )
    ).all()
    roles: dict[uuid.UUID, set[str]] = {}
    for row in rows:
        if _needs_replacement(db, row):
            roles.setdefault(row.workspace_id, set()).add(row.role)
    indexed: list[uuid.UUID] = []
    if "embedding" in only:
        indexed = list(
            db.scalars(
                sa.text(
                    "SELECT DISTINCT workspace_id FROM document_chunks "
                    "WHERE embedding_model_id = :m"
                ),
                {"m": model_id},
            )
        )
    names = _names(db, [*roles, *indexed])
    refs = sorted(
        (
            WorkspaceRef(
                workspace_id,
                names.get(workspace_id, ""),
                [r for r in ROLES if r in used],
                load_settings(db, workspace_id).internal_only,
            )
            for workspace_id, used in roles.items()
        ),
        key=lambda w: w.name.lower(),
    )
    return ModelUsage(
        refs,
        sorted((WorkspaceRef(w, names.get(w, "")) for w in indexed), key=lambda w: w.name.lower()),
    )


def model_usage(
    engine: sa.Engine, model_id: uuid.UUID, roles: tuple[str, ...] = ROLES
) -> ModelUsage:
    """Where the model needs a replacement: the roles in each Workspace where it is the
    default or the only model, and the Workspaces holding documents indexed with it.
    404 ``model_not_found``."""
    with Session(engine) as db:
        if db.get(ModelRecord, model_id) is None:
            raise _not_found()
        return _usage(db, model_id, roles)


def _check_replacement(db: Session, role: str, model_id: uuid.UUID, removed: uuid.UUID) -> None:
    field_name = f"{role}_model_id"
    model = db.get(ModelRecord, model_id)
    if model is None or model_id == removed:
        raise ApiError(
            422,
            "invalid_model_role",
            "Choose another registered model as the replacement.",
            {"field": field_name},
        )
    if role not in model.roles:
        raise ApiError(
            422,
            "invalid_model_role",
            f"That model does not have the {role} role.",
            {"field": field_name},
        )
    if model.test_ok is not True:
        raise ApiError(
            422,
            "model_not_tested",
            'Run "Test connection" on the replacement first.',
            {"field": field_name},
        )


def _ref_json(w: WorkspaceRef, roles: list[str]) -> dict[str, object]:
    return {"id": str(w.id), "name": w.name, "roles": roles, "internal_only": w.internal_only}


def _release(
    db: Session,
    model_id: uuid.UUID,
    roles: tuple[str, ...],
    workspace_replacements: list[WorkspaceReplacement],
) -> None:
    """Free the model of ``roles`` in every Workspace: replace it where it is the default
    or the only model, drop it silently elsewhere. 409 ``embedding_model_indexed`` /
    ``replacement_required`` / ``replacement_not_allowed``."""
    overrides = {w.workspace_id: w for w in workspace_replacements}
    usage = _usage(db, model_id, roles)
    if usage.indexed_workspaces:
        raise ApiError(
            409,
            "embedding_model_indexed",
            "Documents in these Workspaces are indexed with this model, so it cannot be removed.",
            {"workspaces": [{"id": str(w.id), "name": w.name} for w in usage.indexed_workspaces]},
        )
    missing: list[dict[str, object]] = []
    unallowed: list[dict[str, object]] = []
    plan: list[tuple[uuid.UUID, str, uuid.UUID]] = []
    for workspace in usage.workspaces:
        absent: list[str] = []
        bad: list[str] = []
        for role in workspace.roles:
            chosen = getattr(overrides.get(workspace.id), role, None)
            if chosen is None:
                absent.append(role)
                continue
            _check_replacement(db, role, chosen, model_id)
            if workspace.internal_only and not model_is_internal(db, chosen):
                bad.append(role)
                continue
            plan.append((workspace.id, role, chosen))
        if absent:
            missing.append(_ref_json(workspace, absent))
        if bad:
            unallowed.append(_ref_json(workspace, bad))
    if missing:
        raise ApiError(
            409,
            "replacement_required",
            "Choose a replacement for each Workspace that depends on this model.",
            {"workspaces": missing},
        )
    if unallowed:
        raise ApiError(
            409,
            "replacement_not_allowed",
            "The replacement is not allowed in these Workspaces: choose another model for each.",
            {"workspaces": unallowed},
        )
    for workspace_id, role, chosen in plan:
        removed = db.get(WorkspaceModelRecord, (workspace_id, model_id, role))
        assert removed is not None
        was_default = removed.is_default
        db.delete(removed)
        db.flush()
        present = db.get(WorkspaceModelRecord, (workspace_id, chosen, role))
        if present is None:
            db.add(
                WorkspaceModelRecord(
                    workspace_id=workspace_id, model_id=chosen, role=role, is_default=was_default
                )
            )
        elif was_default:
            present.is_default = True
    # What is left are non-default extras: they simply go.
    db.execute(
        sa.delete(WorkspaceModelRecord).where(
            WorkspaceModelRecord.model_id == model_id, WorkspaceModelRecord.role.in_(roles)
        )
    )
    db.flush()


def release_roles(
    engine: sa.Engine,
    model_id: uuid.UUID,
    roles: tuple[str, ...],
    workspace_replacements: list[WorkspaceReplacement],
) -> None:
    """Untick ``roles`` on the model: free them in every Workspace (see ``_release``)."""
    with Session(engine) as db, db.begin():
        if db.get(ModelRecord, model_id, with_for_update=True) is None:
            raise _not_found()
        _release(db, model_id, roles, workspace_replacements)


def remove_model(
    engine: sa.Engine,
    model_id: uuid.UUID,
    *,
    workspace_replacements: list[WorkspaceReplacement],
) -> None:
    """Delete the model, freeing every role in every Workspace first. All or nothing."""
    with Session(engine) as db, db.begin():
        model = db.get(ModelRecord, model_id, with_for_update=True)
        if model is None:
            raise _not_found()
        _release(db, model_id, ROLES, workspace_replacements)
        # Conversations whose last pick was this model fall back to the Workspace's agent
        # model: the foreign key nulls the pick.
        db.delete(model)
