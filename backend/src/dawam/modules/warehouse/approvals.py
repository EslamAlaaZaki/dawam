"""Layer Approval of the Core and Mart Layers (spec §6.10, stories 107, 120).

``ApprovalService`` moves a Layer between ``draft``, ``in_review`` and ``approved``. Mart
can be approved only while Core is approved, and moving Core away from ``approved`` takes
Mart's approval with it. With the Workspace's owner setting on, whoever approves must not
be the Layer's last editor.

Revocation does not rely on each writer: like the score trigger, this module watches what
a session flushes. Any change to a Core or Mart table, column, table mapping, branch or
column mapping (a direct edit, a Change Set item of any origin, propagation) makes its
actor the Layer's last editor; a change to a revoking field (``REVOKING``: structure,
mapping type, inputs, expressions, joins, filters, group-by, lookups) also returns an
approved Layer to ``draft``. Text fields (descriptions, grain, rule text, notes) never
revoke, and staging objects revoke nothing: a staging change reaches Core only through a
propagation item, which is itself a Core change. The revocation is written in the same
transaction, just before it commits, and audited with its cause under the actor and
channel of the change (``audit_context``). A target platform change revokes both Layers.
"""

from __future__ import annotations

import uuid
import weakref
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import AuditContext, audit_context, record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock, system_clock
from dawam.platform.errors import ApiError

from .tables import (
    APPROVAL_LAYERS,
    APPROVAL_STATUSES,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    LayerApprovalRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

ENTITY_TYPE = "layer_approval"
"""The audit trail's entity type of a Layer's approval; its entity id is the Layer."""
CAUSE_LIMIT = 20
"""The changes a revocation's audit entry names; the rest are only counted."""

REVOKING: dict[type, frozenset[str]] = {
    DwTableRecord: frozenset(
        {"name", "layer", "kind", "fact_type", "is_aggregate", "scd_type", "unknown_member"}
        | {"status"}
    ),
    DwColumnRecord: frozenset(
        {"name", "table_id", "data_type", "is_nullable", "role", "scd_type_override"}
        | {"references_table_id", "status"}
    ),
    TableMappingRecord: frozenset({"match_keys"}),
    MappingBranchRecord: frozenset({"driving_input", "joins", "filters", "group_by", "having"}),
    ColumnMappingRecord: frozenset(
        {"mapping_type", "sql_expression", "lookup", "branch_id", "dw_column_id"}
    ),
}
"""The fields whose change returns an approved Layer to draft (spec §6.10 "Revoking fields")."""
BOOKKEEPING = frozenset(
    {"id", "version", "created_at", "updated_at", "updated_by", "validation", "review_flags"}
    | {"edited_fields", "generated", "generation_key"}
)
"""Fields DAWAM maintains alongside an edit: changing only these is no edit."""
OBJECT_TYPES: dict[type, str] = {
    DwTableRecord: "dw_table",
    DwColumnRecord: "dw_column",
    TableMappingRecord: "table_mapping",
    MappingBranchRecord: "mapping_branch",
    ColumnMappingRecord: "column_mapping",
}

_PENDING = "dawam.approvals.pending"
"""``Session.info`` key: the design changes the session flushed, resolved before commit."""


@dataclass(frozen=True)
class DesignChange:
    """A change to a design object, located by whichever id it carries."""

    object_type: str
    object_id: uuid.UUID
    fields: tuple[str, ...]
    """The changed fields, or ``("created",)`` / ``("deleted",)``."""
    structural: bool
    data_warehouse_id: uuid.UUID | None = None
    layer: str | None = None
    table_id: uuid.UUID | None = None
    table_mapping_id: uuid.UUID | None = None


@dataclass(frozen=True)
class LayerApproval:
    layer: str
    status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    last_editor_id: uuid.UUID | None
    last_edited_at: datetime | None


_CLOCKS: weakref.WeakKeyDictionary[sa.Engine, Clock] = weakref.WeakKeyDictionary()


def install_clock(engine: sa.Engine, clock: Clock) -> None:
    """Time revocations made by sessions of ``engine`` with ``clock`` (default: the system's)."""
    _CLOCKS[engine] = clock


def note_design_change(db: Session, change: DesignChange) -> None:
    """Note a change the session makes without a flush that shows it (a bulk statement)."""
    db.info.setdefault(_PENDING, []).append(change)


def _change_of(obj: Any, kind: str) -> DesignChange | None:
    revoking = REVOKING.get(type(obj))
    if revoking is None:
        if (
            isinstance(obj, DataWarehouseRecord)
            and kind == "dirty"
            and sa.inspect(obj).attrs.target_platform.history.has_changes()
        ):
            return DesignChange("data_warehouse", obj.id, ("target_platform",), True, obj.id)
        return None
    if kind == "new":
        if isinstance(obj, TableMappingRecord):
            return None  # an empty mapping DAWAM opens for a table: nothing changed yet
        fields: tuple[str, ...] = ("created",)
        structural = True
    elif kind == "deleted":
        fields, structural = ("deleted",), True
    else:
        state = sa.inspect(obj)
        changed = sorted(
            a.key for a in state.attrs if a.key not in BOOKKEEPING and a.history.has_changes()
        )
        if not changed:
            return None
        fields, structural = tuple(changed), bool(revoking.intersection(changed))
    object_type = OBJECT_TYPES[type(obj)]
    if isinstance(obj, DwTableRecord):
        return DesignChange(
            object_type, obj.id, fields, structural, obj.data_warehouse_id, obj.layer
        )
    if isinstance(obj, DwColumnRecord):
        return DesignChange(object_type, obj.id, fields, structural, table_id=obj.table_id)
    if isinstance(obj, TableMappingRecord):
        return DesignChange(object_type, obj.id, fields, structural, table_id=obj.dw_table_id)
    return DesignChange(
        object_type, obj.id, fields, structural, table_mapping_id=obj.table_mapping_id
    )


def _on_flush(session: Session, _context: object) -> None:
    for kind, objects in (
        ("new", session.new),
        ("dirty", session.dirty),
        ("deleted", session.deleted),
    ):
        for obj in objects:
            change = _change_of(obj, kind)
            if change is not None:
                note_design_change(session, change)


def _before_commit(session: Session) -> None:
    session.flush()  # what is still pending is part of the change too
    changes = session.info.pop(_PENDING, None)
    if not changes:
        return
    engine = session.get_bind()
    clock = _CLOCKS.get(engine, system_clock) if isinstance(engine, sa.Engine) else system_clock
    with session.no_autoflush:
        _apply(session, changes, audit_context(session), clock())


def _forget(session: Session) -> None:
    session.info.pop(_PENDING, None)


sa.event.listen(Session, "after_flush", _on_flush)
sa.event.listen(Session, "before_commit", _before_commit)
sa.event.listen(Session, "after_soft_rollback", lambda session, _previous: _forget(session))


@dataclass
class _LayerChanges:
    edited: bool = False
    structural: list[DesignChange] = field(default_factory=list)


def _locate(
    db: Session, changes: Iterable[DesignChange]
) -> dict[tuple[uuid.UUID, str], _LayerChanges]:
    """The Core and Mart Layers the changes belong to; staging is left out."""
    changes = list(changes)
    table_ids = {c.table_id for c in changes if c.table_id is not None}
    mapping_ids = {c.table_mapping_id for c in changes if c.table_mapping_id is not None}
    tables: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if table_ids:
        for row in db.execute(
            sa.select(DwTableRecord.id, DwTableRecord.data_warehouse_id, DwTableRecord.layer).where(
                DwTableRecord.id.in_(table_ids)
            )
        ):
            tables[row.id] = (row.data_warehouse_id, row.layer)
    mappings: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    if mapping_ids:
        for row in db.execute(
            sa.select(TableMappingRecord.id, DwTableRecord.data_warehouse_id, DwTableRecord.layer)
            .join(DwTableRecord, DwTableRecord.id == TableMappingRecord.dw_table_id)
            .where(TableMappingRecord.id.in_(mapping_ids))
        ):
            mappings[row.id] = (row.data_warehouse_id, row.layer)
    out: dict[tuple[uuid.UUID, str], _LayerChanges] = {}
    for change in changes:
        if change.object_type == "data_warehouse":
            assert change.data_warehouse_id is not None
            places = [(change.data_warehouse_id, layer) for layer in APPROVAL_LAYERS]
        elif change.layer is not None and change.data_warehouse_id is not None:
            places = [(change.data_warehouse_id, change.layer)]
        elif change.table_id is not None and change.table_id in tables:
            places = [tables[change.table_id]]
        elif change.table_mapping_id is not None and change.table_mapping_id in mappings:
            places = [mappings[change.table_mapping_id]]
        else:
            places = []  # gone with its table, whose own change counts
        for place in places:
            if place[1] not in APPROVAL_LAYERS:
                continue
            found = out.setdefault(place, _LayerChanges())
            found.edited = True
            if change.structural:
                found.structural.append(change)
    return out


def _lock(db: Session, data_warehouse_id: uuid.UUID, layer: str, now: datetime):
    """The Layer's approval row, created in ``draft`` if missing, locked ``FOR UPDATE``."""
    db.execute(
        pg_insert(LayerApprovalRecord)
        .values(data_warehouse_id=data_warehouse_id, layer=layer, status="draft", updated_at=now)
        .on_conflict_do_nothing()
    )
    return db.scalars(
        sa.select(LayerApprovalRecord)
        .where(
            LayerApprovalRecord.data_warehouse_id == data_warehouse_id,
            LayerApprovalRecord.layer == layer,
        )
        .with_for_update()
    ).one()


def _cause(changes: list[DesignChange]) -> dict[str, Any]:
    return {
        "reason": "structural_change",
        "changes": [
            {"object_type": c.object_type, "object_id": str(c.object_id), "fields": list(c.fields)}
            for c in changes[:CAUSE_LIMIT]
        ],
        "change_count": len(changes),
    }


def _set_status(
    db: Session,
    row: LayerApprovalRecord,
    status: str,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    now: datetime,
    via: str = "user",
    change_set_id: uuid.UUID | None = None,
    cause: dict[str, Any] | None = None,
) -> None:
    """Move the Layer to ``status`` and audit it (with ``cause`` for a revocation)."""
    old: dict[str, Any] = {"status": row.status}
    row.status = status
    row.approved_by = actor_id if status == "approved" else None
    row.approved_at = now if status == "approved" else None
    row.updated_at = now
    new: dict[str, Any] = {"status": status}
    if cause is not None:
        new["cause"] = cause
    record_audit(
        db,
        workspace_id=workspace_id,
        actor_id=actor_id,
        entity_type=ENTITY_TYPE,
        entity_id=row.layer,
        old=old,
        new=new,
        at=now,
        via=via,
        change_set_id=change_set_id,
    )
    record_activity(
        db,
        workspace_id=workspace_id,
        actor_id=actor_id,
        verb="layer.revoked" if cause is not None else f"layer.{status}",
        object_type=ENTITY_TYPE,
        object_id=row.layer,
        object_label=row.layer.capitalize(),
        details={"from": old["status"], "to": status},
        at=now,
    )


def _revoke_mart(
    db: Session,
    data_warehouse_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    now: datetime,
    via: str = "user",
    change_set_id: uuid.UUID | None = None,
) -> None:
    mart = _lock(db, data_warehouse_id, "mart", now)
    if mart.status == "approved":
        _set_status(
            db,
            mart,
            "draft",
            workspace_id=workspace_id,
            actor_id=actor_id,
            now=now,
            via=via,
            change_set_id=change_set_id,
            cause={"reason": "core_revoked"},
        )


def _apply(
    db: Session, changes: list[DesignChange], context: AuditContext | None, now: datetime
) -> None:
    located = _locate(db, changes)
    actor_id = context.actor_id if context else None
    via = context.via if context else "user"
    change_set_id = context.change_set_id if context else None
    # Core before Mart: one lock order for every writer, and Core's revocation reaches Mart.
    for data_warehouse_id, layer in sorted(located, key=lambda p: (p[0], p[1] != "core")):
        found = located[(data_warehouse_id, layer)]
        workspace_id = db.scalar(
            sa.select(DataWarehouseRecord.workspace_id).where(
                DataWarehouseRecord.id == data_warehouse_id
            )
        )
        if workspace_id is None:
            continue  # the Data Warehouse is going with its Workspace
        row = _lock(db, data_warehouse_id, layer, now)
        if actor_id is not None and found.edited:
            row.last_editor_id = actor_id
            row.last_edited_at = now
        if not found.structural or row.status != "approved":
            continue
        _set_status(
            db,
            row,
            "draft",
            workspace_id=workspace_id,
            actor_id=actor_id,
            now=now,
            via=via,
            change_set_id=change_set_id,
            cause=_cause(found.structural),
        )
        if layer == "core":
            _revoke_mart(
                db,
                data_warehouse_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                now=now,
                via=via,
                change_set_id=change_set_id,
            )


def _view(layer: str, row: LayerApprovalRecord | None) -> LayerApproval:
    if row is None:
        return LayerApproval(layer, "draft", None, None, None, None)
    return LayerApproval(
        layer=row.layer,
        status=row.status,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        last_editor_id=row.last_editor_id,
        last_edited_at=row.last_edited_at,
    )


class ApprovalService:
    """Reading and moving the Layer Approval of Core and Mart. Every method authorizes."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def list(self, user: User, workspace_id: uuid.UUID) -> list[LayerApproval]:
        """Core's and Mart's approval (any member). 404 ``not_set_up`` before setup."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            warehouse = _warehouse(db, workspace_id)
            rows = {
                r.layer: r
                for r in db.scalars(
                    sa.select(LayerApprovalRecord).where(
                        LayerApprovalRecord.data_warehouse_id == warehouse.id
                    )
                )
            }
            return [_view(layer, rows.get(layer)) for layer in APPROVAL_LAYERS]

    def move(self, user: User, workspace_id: uuid.UUID, layer: str, status: str) -> LayerApproval:
        """Move the Core or Mart Layer to ``status`` (owners and editors). 422
        ``no_layer_approval`` for staging; 409 ``core_not_approved`` when approving Mart while
        Core is not approved; 409 ``approver_is_last_editor`` when the Workspace requires a
        different approver and ``user`` last edited the Layer. Moving Core away from
        ``approved`` returns an approved Mart to ``draft``."""
        self._workspaces.authorize(user, Action.APPROVE_LAYER, workspace_id)
        if layer not in APPROVAL_LAYERS:
            raise ApiError(
                422, "no_layer_approval", "Only the Core and Mart Layers have an approval."
            )
        if status not in APPROVAL_STATUSES:
            raise ApiError(422, "validation_error", f"Unknown status {status!r}.")
        distinct = status == "approved" and self._workspaces.requires_distinct_approver(
            workspace_id
        )
        with Session(self._engine) as db, db.begin():
            warehouse = _warehouse(db, workspace_id)
            now = self._clock()
            core = _lock(db, warehouse.id, "core", now)
            row = core if layer == "core" else _lock(db, warehouse.id, "mart", now)
            if row.status == status:
                return _view(layer, row)
            if status == "approved":
                if layer == "mart" and core.status != "approved":
                    raise ApiError(
                        409,
                        "core_not_approved",
                        "The Mart Layer can be approved only while the Core Layer is approved.",
                    )
                if distinct and row.last_editor_id == user.id:
                    raise ApiError(
                        409,
                        "approver_is_last_editor",
                        "This Workspace requires the approver not to be the Layer's last "
                        "editor; ask another editor or owner to approve it.",
                    )
            was_approved = row.status == "approved"
            _set_status(db, row, status, workspace_id=workspace_id, actor_id=user.id, now=now)
            if layer == "core" and was_approved:
                _revoke_mart(db, warehouse.id, workspace_id=workspace_id, actor_id=user.id, now=now)
            db.flush()
            return _view(layer, row)


def _warehouse(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord:
    warehouse = db.scalars(
        sa.select(DataWarehouseRecord).where(DataWarehouseRecord.workspace_id == workspace_id)
    ).first()
    if warehouse is None:
        raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
    return warehouse
