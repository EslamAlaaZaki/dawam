"""KPI links and formula SQL as Change Set items (spec §6.13, story 76a).

``KpiLinkHandler`` is the ``changesets`` engine's handler for ``kpi`` objects: an item
updates a KPI's ``formula_sql`` and/or its ``links`` (the ids of the DW columns it uses). The
engine decides staleness (per changed field), role, ordering and audit; this class checks the
values against the DW Schema and changes the KPI.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.changesets import AppliedChange
from dawam.modules.warehouse import read_schema
from dawam.modules.workspaces import Action
from dawam.platform.errors import ApiError

from .links import check_links, link_ids, validate_formula, write_links
from .tables import SQL_MAX_LENGTH, KpiRecord

FIELDS = ("formula_sql", "links")


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_change_set", message)


def _ids(value: Any) -> list[uuid.UUID]:
    if not isinstance(value, list):
        raise _invalid("`links` is a list of DW column ids.")
    try:
        return [uuid.UUID(str(v)) for v in value]
    except ValueError:
        raise _invalid("`links` is a list of DW column ids.") from None


def _sql(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > SQL_MAX_LENGTH:
        raise _invalid(f"`formula_sql` is SQL text of at most {SQL_MAX_LENGTH} characters.")
    return value.strip()


class KpiLinkHandler:
    object_type = "kpi"

    def required_action(self, operation: str) -> Action:
        if operation != "update":
            raise ValueError("Change Sets only update KPIs' links and formula SQL.")
        return Action.EDIT_KPI

    def validate(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
    ) -> None:
        unknown = [f for f in payload if f not in FIELDS]
        if unknown or not payload:
            raise _invalid(
                f"A kpi item changes some of {', '.join(FIELDS)}"
                + (f", not {', '.join(unknown)}" if unknown else "")
                + "."
            )
        if self._record(db, workspace_id, object_id) is None:
            raise _invalid("The KPI does not exist.")
        self._check(db, workspace_id, payload)

    def current_values(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID,
        fields: Sequence[str],
    ) -> dict[str, Any] | None:
        record = self._record(db, workspace_id, object_id, lock=True)
        return None if record is None else self._snapshot(db, record, fields)

    def apply(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        operation: str,
        object_id: uuid.UUID | None,
        payload: Mapping[str, Any],
        *,
        at: datetime,
    ) -> AppliedChange:
        record = self._record(db, workspace_id, object_id, lock=True)
        if record is None:  # pragma: no cover - the engine checked staleness under the lock
            raise ApiError(409, "version_conflict", "The KPI no longer exists.")
        columns = self._check(db, workspace_id, payload)
        before = self._snapshot(db, record, (*FIELDS, "status"))
        if "formula_sql" in payload:
            record.formula_sql = _sql(payload["formula_sql"])
        if columns is not None:
            write_links(db, record.id, [c.id for c in columns])
        if record.status == "approved" and self._snapshot(db, record, FIELDS) != {
            f: before[f] for f in FIELDS
        }:
            record.status = "draft"
        after = self._snapshot(db, record, (*FIELDS, "status"))
        changed = [f for f in (*FIELDS, "status") if before[f] != after[f]]
        if changed:
            record.version += 1
            record.updated_at = at
        db.flush()
        return AppliedChange(
            entity_type="kpi",
            entity_id=record.id,
            old={f: before[f] for f in changed},
            new={f: after[f] for f in changed},
            label=record.name,
        )

    def _check(self, db: Session, workspace_id: uuid.UUID, payload: Mapping[str, Any]):
        """Refuse values the DW Schema cannot satisfy; the checked link columns, if any."""
        if "formula_sql" in payload:
            validate_formula(read_schema(db, workspace_id), _sql(payload["formula_sql"]))
        if "links" not in payload:
            return None
        try:
            return check_links(db, workspace_id, _ids(payload["links"]))
        except ApiError as exc:
            if exc.code == "invalid_change_set":
                raise
            raise _invalid(exc.message) from None

    def _snapshot(self, db: Session, record: KpiRecord, fields: Sequence[str]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in fields:
            if field == "links":
                values[field] = sorted(str(i) for i in link_ids(db, record.id))
            else:
                values[field] = getattr(record, field)
        return values

    def _record(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        object_id: uuid.UUID | None,
        *,
        lock: bool = False,
    ) -> KpiRecord | None:
        if object_id is None:
            return None
        query = sa.select(KpiRecord).where(
            KpiRecord.id == object_id, KpiRecord.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        return db.scalars(query).first()
