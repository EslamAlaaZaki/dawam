from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.sources import SourceSystemService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .tables import (
    AGGREGATION_MAX_LENGTH,
    MAX_TARGETS,
    NAME_MAX_LENGTH,
    OWNER_MAX_LENGTH,
    REFRESH_MAX_LENGTH,
    SQL_MAX_LENGTH,
    STATUSES,
    TARGET_TEXT_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    UNIT_MAX_LENGTH,
    KpiRecord,
)

AUDITED_FIELDS = (
    "source_system_id",
    "name",
    "definition",
    "formula_text",
    "formula_sql",
    "unit",
    "aggregation",
    "owner",
    "refresh_frequency",
    "targets",
    "status",
)
"""The fields of a KPI the audit trail records (all of them but bookkeeping)."""

_TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "definition": ("business definition", TEXT_MAX_LENGTH),
    "formula_text": ("formula in words", TEXT_MAX_LENGTH),
    "unit": ("unit", UNIT_MAX_LENGTH),
    "aggregation": ("aggregation", AGGREGATION_MAX_LENGTH),
    "owner": ("owner", OWNER_MAX_LENGTH),
    "refresh_frequency": ("refresh frequency", REFRESH_MAX_LENGTH),
}


@dataclass(frozen=True)
class KpiTarget:
    label: str
    value: str


@dataclass(frozen=True)
class Kpi:
    id: uuid.UUID
    workspace_id: uuid.UUID
    source_system_id: uuid.UUID | None
    """``None``: a Data Warehouse KPI."""
    name: str
    definition: str
    formula_text: str
    formula_sql: str | None
    unit: str
    aggregation: str
    owner: str
    refresh_frequency: str
    targets: list[KpiTarget]
    origin: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class KpiPage:
    items: list[Kpi]
    next_cursor: str | None


def _view(record: KpiRecord) -> Kpi:
    return Kpi(
        id=record.id,
        workspace_id=record.workspace_id,
        source_system_id=record.source_system_id,
        name=record.name,
        definition=record.definition,
        formula_text=record.formula_text,
        formula_sql=record.formula_sql,
        unit=record.unit,
        aggregation=record.aggregation,
        owner=record.owner,
        refresh_frequency=record.refresh_frequency,
        targets=[KpiTarget(label=t["label"], value=t["value"]) for t in record.targets],
        origin=record.origin,
        status=record.status,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _snapshot(record: KpiRecord) -> dict[str, Any]:
    """The audited fields as JSON."""
    return {
        field: str(value) if isinstance(value := getattr(record, field), uuid.UUID) else value
        for field in AUDITED_FIELDS
    }


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "KPI not found.")


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_kpi", message, {"field": field})


def _clean(value: str, label: str, field: str, max_length: int, *, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise _invalid(f"The {label} must not be empty.", field)
    if len(cleaned) > max_length:
        raise _invalid(f"The {label} must be at most {max_length} characters.", field)
    return cleaned


def _clean_targets(targets: Sequence[KpiTarget]) -> list[dict[str, str]]:
    if len(targets) > MAX_TARGETS:
        raise _invalid(f"A KPI has at most {MAX_TARGETS} targets.", "targets")
    return [
        {
            "label": _clean(
                t.label, "target label", "targets", TARGET_TEXT_MAX_LENGTH, required=True
            ),
            "value": _clean(
                t.value, "target value", "targets", TARGET_TEXT_MAX_LENGTH, required=True
            ),
        }
        for t in targets
    ]


def _clean_status(status: str) -> str:
    if status not in STATUSES:
        raise _invalid(f"The status must be one of {', '.join(STATUSES)}.", "status")
    return status


class KpiService:
    """KPIs of a Workspace. Every method that acts for a user authorizes through the
    workspaces module's policy first; callers add no checks of their own."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        systems: SourceSystemService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._systems = systems
        self._clock = clock

    def create(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        name: str,
        source_system_id: uuid.UUID | None = None,
        definition: str = "",
        formula_text: str = "",
        formula_sql: str | None = None,
        unit: str = "",
        aggregation: str = "",
        owner: str = "",
        refresh_frequency: str = "",
        targets: Sequence[KpiTarget] = (),
    ) -> Kpi:
        """Document a KPI as a draft, under a Source System or (``source_system_id``
        ``None``) the Data Warehouse, which needs no setup. 404 if the Source System is
        not in the Workspace; 422 ``invalid_kpi``."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        if source_system_id is not None:
            self._systems.get(user, workspace_id, source_system_id)
        now = self._clock()
        record = KpiRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            source_system_id=source_system_id,
            name=_clean(name, "name", "name", NAME_MAX_LENGTH, required=True),
            origin="user",
            status="draft",
            created_by=user.id,
            created_at=now,
            updated_at=now,
            version=1,
            targets=_clean_targets(targets),
            formula_sql=_clean_sql(formula_sql),
            **{
                field: _clean(value, label, field, limit)
                for field, value, (label, limit) in (
                    (f, v, _TEXT_FIELDS[f])
                    for f, v in (
                        ("definition", definition),
                        ("formula_text", formula_text),
                        ("unit", unit),
                        ("aggregation", aggregation),
                        ("owner", owner),
                        ("refresh_frequency", refresh_frequency),
                    )
                )
            },
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old=None,
                new=_snapshot(record),
                at=now,
            )
            self._record_activity(db, user, "kpi.created", record)
            return _view(record)

    def list(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        source_system_id: uuid.UUID | None = None,
        data_warehouse: bool = False,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> KpiPage:
        """The Workspace's KPIs (any member), by name; narrowed to one Source System's or
        to the Data Warehouse's. 422 ``invalid_filter`` if both are asked for."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        if source_system_id is not None and data_warehouse:
            raise ApiError(
                422,
                "invalid_filter",
                "Ask for one Source System's KPIs or the Data Warehouse's, not both.",
            )
        if source_system_id is not None:
            self._systems.get(user, workspace_id, source_system_id)
        query = (
            sa.select(KpiRecord)
            .where(KpiRecord.workspace_id == workspace_id)
            .order_by(KpiRecord.name, KpiRecord.id)
            .limit(limit + 1)
        )
        if source_system_id is not None:
            query = query.where(KpiRecord.source_system_id == source_system_id)
        elif data_warehouse:
            query = query.where(KpiRecord.source_system_id.is_(None))
        if cursor is not None:
            after_name, after_id = decode_cursor(cursor, 2)
            try:
                after = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(
                    422, "invalid_cursor", "The cursor is not valid; start from the first page."
                ) from None
            query = query.where(sa.tuple_(KpiRecord.name, KpiRecord.id) > (after_name, after))
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        page = records[:limit]
        next_cursor = (
            encode_cursor(page[-1].name, str(page[-1].id)) if len(records) > limit else None
        )
        return KpiPage(items=[_view(r) for r in page], next_cursor=next_cursor)

    def get(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> Kpi:
        """Open a KPI (any member). 404 if it is not in ``workspace_id``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return _view(self._load(db, workspace_id, kpi_id))

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> Kpi:
        """Edit a KPI. ``changes`` holds only the fields to change (any of ``name``,
        ``definition``, ``formula_text``, ``formula_sql`` (``None`` clears it), ``unit``,
        ``aggregation``, ``owner``, ``refresh_frequency``, ``targets`` (a list of
        ``KpiTarget``) and ``status``); the others stay. A stale ``version`` is 409
        ``version_conflict``."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            if record.version != version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "Someone else changed this KPI since you loaded it. Reload and try again.",
                    {"current_version": record.version},
                )
            before = _snapshot(record)
            for field, value in changes.items():
                if field == "name":
                    record.name = _clean(value, "name", "name", NAME_MAX_LENGTH, required=True)
                elif field in _TEXT_FIELDS:
                    label, limit = _TEXT_FIELDS[field]
                    setattr(record, field, _clean(value, label, field, limit))
                elif field == "formula_sql":
                    record.formula_sql = _clean_sql(value)
                elif field == "targets":
                    record.targets = _clean_targets(value)
                elif field == "status":
                    record.status = _clean_status(value)
                else:  # pragma: no cover - a programming error
                    raise ValueError(f"cannot change KPI field {field!r}")
            after = _snapshot(record)
            changed = [f for f in AUDITED_FIELDS if before[f] != after[f]]
            if not changed:
                return _view(record)
            record.version += 1
            now = self._clock()
            record.updated_at = now
            db.flush()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old={f: before[f] for f in changed},
                new={f: after[f] for f in changed},
                at=now,
            )
            self._record_activity(
                db,
                user,
                "kpi.status_changed" if changed == ["status"] else "kpi.edited",
                record,
                details={"fields": changed},
            )
            return _view(record)

    def delete(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> None:
        """Delete a KPI (owners and editors). The audit trail keeps what it held."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            now = self._clock()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old=_snapshot(record),
                new=None,
                at=now,
            )
            self._record_activity(db, user, "kpi.deleted", record)
            db.delete(record)

    def _load(
        self, db: Session, workspace_id: uuid.UUID, kpi_id: uuid.UUID, *, lock: bool = False
    ) -> KpiRecord:
        query = sa.select(KpiRecord).where(KpiRecord.id == kpi_id)
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        # A KPI under another Workspace's URL is as missing as one that does not exist.
        if record is None or record.workspace_id != workspace_id:
            raise _not_found()
        return record

    def _record_activity(
        self,
        db: Session,
        user: User,
        verb: str,
        record: KpiRecord,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        record_activity(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            verb=verb,
            object_type="kpi",
            object_id=record.id,
            object_label=record.name,
            details={
                "source_system_id": str(record.source_system_id)
                if record.source_system_id
                else None,
                **(details or {}),
            },
            at=self._clock(),
        )


def _clean_sql(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > SQL_MAX_LENGTH:
        raise _invalid(
            f"The formula SQL must be at most {SQL_MAX_LENGTH} characters.", "formula_sql"
        )
    return cleaned or None
