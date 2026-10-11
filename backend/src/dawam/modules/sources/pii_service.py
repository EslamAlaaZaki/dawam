"""The PII review queue (spec §6.12, stories 133, 134, 135).

Name rules record a ``suggested`` finding for every suspected column of each new Snapshot
(``internal.pii``). Owners and editors list the findings and ``confirm`` or ``dismiss``
them. Confirming sets the column's sensitive flag and PII category; dismissing removes
protection only when the column is not flagged sensitive (``internal.pii.is_protected``
is the one definition). Every decision is audited (``via=user``) and recorded in the
activity feed, in the transaction that makes it. Findings never hold a data value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .internal.pii import is_protected
from .tables import (
    PII_STATUSES,
    PiiFindingRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)


@dataclass(frozen=True)
class PiiFinding:
    id: uuid.UUID
    column_id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    rule: str
    category: str
    confidence: float
    evidence: str
    status: str
    """``suggested``, ``confirmed`` or ``dismissed``."""
    is_sensitive: bool
    """The column's own flag."""
    is_protected: bool
    """By the one Protected Column policy."""
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


class PiiService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def list_findings(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        status: str | None = None,
    ) -> list[PiiFinding]:
        """The Source System's PII findings, most confident first; ``status`` narrows to
        one state (the review queue is ``suggested``)."""
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        if status is not None and status not in PII_STATUSES:
            raise ApiError(
                422, "invalid_status", f"The status must be one of {', '.join(PII_STATUSES)}."
            )
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            query = (
                self._select()
                .where(SrcDbSchemaRecord.source_system_id == system_id)
                .order_by(
                    PiiFindingRecord.confidence.desc(),
                    SrcDbSchemaRecord.name,
                    SrcTableRecord.name,
                    SrcColumnRecord.name,
                    PiiFindingRecord.rule,
                )
            )
            if status is not None:
                query = query.where(PiiFindingRecord.status == status)
            rows = db.execute(query).all()
            statuses: dict[uuid.UUID, list[str]] = {}
            for column_id, found in db.execute(
                sa.select(PiiFindingRecord.src_column_id, PiiFindingRecord.status).where(
                    PiiFindingRecord.src_column_id.in_({row[1].id for row in rows})
                )
            ):
                statuses.setdefault(column_id, []).append(found)
            return [self._view(*row, statuses=statuses.get(row[1].id)) for row in rows]

    def confirm(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, finding_id: uuid.UUID
    ) -> PiiFinding:
        """Confirm a finding: the column becomes ``is_sensitive`` with the finding's PII
        category."""
        return self._decide(user, workspace_id, system_id, finding_id, "confirmed")

    def dismiss(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, finding_id: uuid.UUID
    ) -> PiiFinding:
        """Dismiss a finding. The column stays protected only if it is ``is_sensitive``."""
        return self._decide(user, workspace_id, system_id, finding_id, "dismissed")

    # -- internals ----------------------------------------------------------------------

    def _select(self) -> sa.Select:
        return (
            sa.select(PiiFindingRecord, SrcColumnRecord, SrcTableRecord, SrcDbSchemaRecord.name)
            .join(SrcColumnRecord, SrcColumnRecord.id == PiiFindingRecord.src_column_id)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(SrcColumnRecord.status != "deleted")
        )

    def _view(
        self,
        finding: PiiFindingRecord,
        column: SrcColumnRecord,
        table: SrcTableRecord,
        db_schema: str,
        *,
        statuses: list[str] | None = None,
    ) -> PiiFinding:
        return PiiFinding(
            id=finding.id,
            column_id=column.id,
            table_id=table.id,
            db_schema=db_schema,
            table=table.name,
            column=column.name,
            rule=finding.rule,
            category=finding.category,
            confidence=finding.confidence,
            evidence=finding.evidence,
            status=finding.status,
            is_sensitive=column.is_sensitive,
            is_protected=is_protected(
                is_sensitive=column.is_sensitive,
                finding_statuses=statuses if statuses is not None else [finding.status],
            ),
            detected_at=finding.detected_at,
            decided_by=finding.decided_by,
            decided_at=finding.decided_at,
        )

    def _system(
        self, db: Session, workspace_id: uuid.UUID, system_id: uuid.UUID
    ) -> SourceSystemRecord:
        system = db.get(SourceSystemRecord, system_id)
        if system is None or system.status != "present" or system.workspace_id != workspace_id:
            raise _not_found("Source System")
        return system

    def _decide(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        finding_id: uuid.UUID,
        decision: str,
    ) -> PiiFinding:
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            row = db.execute(
                self._select()
                .where(
                    PiiFindingRecord.id == finding_id,
                    SrcDbSchemaRecord.source_system_id == system_id,
                )
                .with_for_update(of=[PiiFindingRecord, SrcColumnRecord])
            ).first()
            if row is None:
                raise _not_found("PII finding")
            finding, column, table, db_schema = row
            now = self._clock()
            old_status = finding.status
            if old_status != decision:
                finding.status = decision
                finding.decided_by = user.id
                finding.decided_at = now
                record_audit(
                    db,
                    workspace_id=system.workspace_id,
                    actor_id=user.id,
                    entity_type="pii_finding",
                    entity_id=finding.id,
                    old={"status": old_status},
                    new={"status": decision},
                    at=now,
                )
                record_activity(
                    db,
                    workspace_id=system.workspace_id,
                    actor_id=user.id,
                    verb=f"pii_finding.{decision}",
                    object_type="pii_finding",
                    object_id=finding.id,
                    object_label=column.name,
                    details={
                        "source_system_id": str(system.id),
                        "table": table.name,
                        "rule": finding.rule,
                        "category": finding.category,
                    },
                    at=now,
                )
            if decision == "confirmed":
                self._flag(db, user, system, column, finding.category, now)
            db.flush()
            statuses = list(
                db.scalars(
                    sa.select(PiiFindingRecord.status).where(
                        PiiFindingRecord.src_column_id == column.id
                    )
                )
            )
            return self._view(finding, column, table, db_schema, statuses=statuses)

    def _flag(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        column: SrcColumnRecord,
        category: str,
        now: datetime,
    ) -> None:
        old = {"is_sensitive": column.is_sensitive, "pii_category": column.pii_category}
        new = {"is_sensitive": True, "pii_category": category}
        changed = [f for f in new if old[f] != new[f]]
        if not changed:
            return
        column.is_sensitive = True
        column.pii_category = category
        column.version += 1
        record_audit(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            entity_type="source_column",
            entity_id=column.id,
            old={f: old[f] for f in changed},
            new={f: new[f] for f in changed},
            at=now,
        )


def confirmed_pii_column_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """The Workspace's source columns with a ``confirmed`` PII finding, in the caller's
    session (no permission check); the start of PII propagation through lineage."""
    return set(
        db.scalars(
            sa.select(PiiFindingRecord.src_column_id)
            .join(SrcColumnRecord, SrcColumnRecord.id == PiiFindingRecord.src_column_id)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
            .where(
                PiiFindingRecord.status == "confirmed",
                SrcColumnRecord.status != "deleted",
                SourceSystemRecord.workspace_id == workspace_id,
            )
        )
    )
