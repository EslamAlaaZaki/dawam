"""Removing a model that is in use (spec §6.18, ADR 0005): the admin names one replacement
per role the model fills, applied to every Workspace that has it among its Workspace Models
in one transaction. An internal-only Workspace takes only an internal replacement, else it
needs its own. A Workspace's indexed documents pin its embedding model, so that removal is
refused while any exist; nothing is ever re-indexed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.errors import ApiError

from .tables import ModelRecord, WorkspaceModelRecord
from .workspace_ai import load_settings, model_is_internal

ROLES = ("agent", "light", "embedding")


@dataclass(frozen=True)
class WorkspaceRef:
    id: uuid.UUID
    name: str
    roles: list[str] = field(default_factory=list)
    """The roles the model fills in this Workspace."""


@dataclass(frozen=True)
class ModelUsage:
    workspaces: list[WorkspaceRef]
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


def _usage(db: Session, model_id: uuid.UUID) -> ModelUsage:
    rows = db.scalars(
        sa.select(WorkspaceModelRecord).where(WorkspaceModelRecord.model_id == model_id)
    ).all()
    roles: dict[uuid.UUID, set[str]] = {}
    for row in rows:
        roles.setdefault(row.workspace_id, set()).add(row.role)
    indexed = list(
        db.scalars(
            sa.text(
                "SELECT DISTINCT workspace_id FROM document_chunks WHERE embedding_model_id = :m"
            ),
            {"m": model_id},
        )
    )
    names = _names(db, [*roles, *indexed])
    refs = sorted(
        (
            WorkspaceRef(workspace_id, names.get(workspace_id, ""), [r for r in ROLES if r in used])
            for workspace_id, used in roles.items()
        ),
        key=lambda w: w.name.lower(),
    )
    return ModelUsage(
        refs,
        sorted((WorkspaceRef(w, names.get(w, "")) for w in indexed), key=lambda w: w.name.lower()),
    )


def model_usage(engine: sa.Engine, model_id: uuid.UUID) -> ModelUsage:
    """Where the model is used: the roles it fills in each Workspace, and the Workspaces
    holding documents indexed with it. 404 ``model_not_found``."""
    with Session(engine) as db:
        if db.get(ModelRecord, model_id) is None:
            raise _not_found()
        return _usage(db, model_id)


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


def remove_model(
    engine: sa.Engine,
    model_id: uuid.UUID,
    *,
    replacements: dict[str, uuid.UUID],
    workspace_replacements: list[WorkspaceReplacement],
) -> None:
    """Delete the model, moving every role it fills to its replacement first: the
    replacement takes its place among the Workspace's models, as the default when it was.
    All or nothing. 409 ``embedding_model_indexed`` / ``replacement_not_allowed``, 422
    ``replacement_required``."""
    overrides = {w.workspace_id: w for w in workspace_replacements}
    with Session(engine) as db, db.begin():
        model = db.get(ModelRecord, model_id, with_for_update=True)
        if model is None:
            raise _not_found()
        usage = _usage(db, model_id)
        if usage.indexed_workspaces:
            raise ApiError(
                409,
                "embedding_model_indexed",
                "Documents in these Workspaces are indexed with this model, so it cannot be "
                "removed.",
                {
                    "workspaces": [
                        {"id": str(w.id), "name": w.name} for w in usage.indexed_workspaces
                    ]
                },
            )
        missing = [r for r in usage.roles if r not in replacements]
        if missing:
            raise ApiError(
                422,
                "replacement_required",
                "Choose a replacement for each role this model fills.",
                {"roles": missing},
            )
        for role in usage.roles:
            _check_replacement(db, role, replacements[role], model_id)
        unallowed: list[dict[str, object]] = []
        plan: list[tuple[uuid.UUID, str, uuid.UUID]] = []
        for workspace in usage.workspaces:
            internal_only = load_settings(db, workspace.id).internal_only
            bad: list[str] = []
            for role in workspace.roles:
                chosen = replacements[role]
                if internal_only and not model_is_internal(db, chosen):
                    chosen = getattr(overrides.get(workspace.id), role, None)
                    if chosen is not None:
                        _check_replacement(db, role, chosen, model_id)
                    if chosen is None or (internal_only and not model_is_internal(db, chosen)):
                        bad.append(role)
                        continue
                plan.append((workspace.id, role, chosen))
            if bad:
                unallowed.append({"id": str(workspace.id), "name": workspace.name, "roles": bad})
        if unallowed:
            raise ApiError(
                409,
                "replacement_not_allowed",
                "The replacement is not allowed in these Workspaces: choose another model "
                "for each.",
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
                        workspace_id=workspace_id,
                        model_id=chosen,
                        role=role,
                        is_default=was_default,
                    )
                )
            elif was_default:
                present.is_default = True
        db.flush()
        # Conversations whose last pick was this model fall back to the Workspace's agent
        # model: the foreign key nulls the pick.
        db.delete(model)
