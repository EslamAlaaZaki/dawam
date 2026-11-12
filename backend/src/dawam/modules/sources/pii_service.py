"""The PII folder of a Source System (spec §6.12, stories 133, 134, 135).

Name rules record a ``suggested`` finding for every suspected column of each new Snapshot
(``internal.pii``); value scans and query results add more. A column can hold many findings
(one per rule) and has one PII category. Owners and editors review by **column**: accepting
or rejecting a column moves every finding on it together. Accepting sets the column's
sensitive flag and the category the reviewer picked; rejecting removes protection only when
the column is not flagged sensitive (``internal.pii.is_protected`` is the one definition).
They can also add a column as PII by hand (no finding) and remove PII from a column, which
rejects its findings and clears the flag; a column whose staging mapping is hashed cannot
lose its PII. A column that becomes PII loses its stored profile values (``min``, ``max``,
top values). Every decision is audited (``via=user``) and recorded in the activity feed, in
the transaction that makes it. Findings never hold a data value.

The folder's tabs: **PII** (accepted: flagged sensitive or an accepted finding),
**Suggested** (not PII, a finding waits for review), **Accepted** (PII through accepted
findings, not hand-only) and **Rejected** (not PII, every finding rejected). Any member may
read the PII tab (names and ratios only); the other tabs and every change are for owners
and editors.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

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
    PII_CATEGORIES,
    PII_STATUSES,
    ColumnProfileRecord,
    ConnectionRecord,
    PiiFindingRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

PII_TABS = ("pii", "suggested", "accepted", "rejected")
PiiTab = Literal["pii", "suggested", "accepted", "rejected"]

HashedMappings = Callable[[Session, Collection[uuid.UUID]], Mapping[uuid.UUID, str]]
"""Which of these source columns have a ``hashed`` staging mapping, and its name
(``staging_table.column``). The warehouse module owns the mappings; the composition root
passes its lookup in, so this module never imports it."""


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
    """``suggested``, ``accepted`` or ``rejected``."""
    is_sensitive: bool
    """The column's own flag."""
    is_protected: bool
    """By the one Protected Column policy."""
    detected_at: datetime
    decided_by: uuid.UUID | None
    decided_at: datetime | None


@dataclass(frozen=True)
class PiiRuleMatch:
    """One finding of a PII column: the rule that matched it."""

    id: uuid.UUID
    source: str
    """``name`` (a name scan) or ``value`` (a value scan or a query's values)."""
    rule: str
    category: str
    confidence: float
    evidence: str
    status: str


@dataclass(frozen=True)
class PiiColumn:
    """A column of the PII folder, with every rule that matched it."""

    id: uuid.UUID
    table_id: uuid.UUID
    db_schema: str
    table: str
    column: str
    data_type: str | None
    source_status: str
    """``present``, or ``source_removed`` / ``out_of_scope`` (gone from the source)."""
    state: Literal["accepted", "suggested", "rejected"]
    category: str | None
    """The column's PII category once accepted; else its most confident rule's."""
    is_sensitive: bool
    rules: list[PiiRuleMatch]
    """Most confident first."""
    manual: bool
    """Added as PII by hand, or flagged sensitive with no accepted finding."""
    decision: Literal["accepted", "added", "rejected", "removed"] | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    rejected_before_by: uuid.UUID | None
    """A suggested column that was rejected before: who rejected it."""
    rejected_before_at: datetime | None
    hashed_mapping: str | None
    """The staging mapping that hashes the column (``staging_table.column``), if any."""
    table_profiled_at: datetime | None
    """When the column's table was last profiled."""

    @property
    def best_confidence(self) -> float:
        return max((r.confidence for r in self.rules), default=0.0)


@dataclass(frozen=True)
class PiiRemoval:
    column: PiiColumn | None
    """The column, now rejected; ``None`` for a column that was PII by hand only."""
    table_id: uuid.UUID


@dataclass(frozen=True)
class PiiColumnList:
    items: list[PiiColumn]
    counts: dict[str, int]
    """Columns per tab: ``pii``, ``suggested``, ``accepted``, ``rejected``."""
    can_profile: bool
    """The Source System has a live Connection, so a table can be profiled again."""


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def _stale() -> ApiError:
    return ApiError(
        409,
        "pii_review_stale",
        "Someone else already reviewed this column. Reload and try again.",
    )


def _tab_of(column: PiiColumn) -> set[str]:
    if column.state == "accepted":
        accepted = any(r.status == "accepted" for r in column.rules)
        return {"pii", "accepted"} if accepted else {"pii"}
    return {column.state}


class PiiService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        hashed_mappings: HashedMappings | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._hashed_mappings = hashed_mappings

    # -- reading ------------------------------------------------------------------------

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

    def list_columns(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        *,
        tab: str = "pii",
    ) -> PiiColumnList:
        """One tab of the PII folder with the count of every tab. Any member may read the
        ``pii`` tab (names and ratios only); the review tabs are for owners and editors."""
        if tab not in PII_TABS:
            raise ApiError(422, "invalid_tab", f"The tab must be one of {', '.join(PII_TABS)}.")
        self._workspaces.authorize(
            user, Action.VIEW_WORKSPACE if tab == "pii" else Action.VIEW_PII_REVIEW, workspace_id
        )
        with Session(self._engine) as db:
            self._system(db, workspace_id, system_id)
            columns = self._columns(db, system_id)
            counts = dict.fromkeys(PII_TABS, 0)
            for column in columns:
                for name in _tab_of(column):
                    counts[name] += 1
            items = [c for c in columns if tab in _tab_of(c)]
            if tab == "pii":
                items.sort(key=lambda c: (c.db_schema, c.table, c.column))
            else:
                items.sort(key=lambda c: (-c.best_confidence, c.db_schema, c.table, c.column))
            can_profile = (
                db.scalar(
                    sa.select(ConnectionRecord.id).where(
                        ConnectionRecord.source_system_id == system_id
                    )
                )
                is not None
            )
            return PiiColumnList(items=items, counts=counts, can_profile=can_profile)

    # -- deciding -----------------------------------------------------------------------

    def accept(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        category: str,
    ) -> PiiColumn:
        """Accept a suggested column as PII with ``category``: every finding on it is
        accepted, the column is flagged sensitive and loses its stored profile values.
        409 ``pii_review_stale`` unless the column is suggested."""
        return self._accept(user, workspace_id, system_id, column_id, category, "suggested")

    def accept_rejected(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        category: str,
    ) -> PiiColumn:
        """Accept a rejected column as PII after all, as ``accept``. 409
        ``pii_review_stale`` unless the column is rejected."""
        return self._accept(user, workspace_id, system_id, column_id, category, "rejected")

    def reject(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, column_id: uuid.UUID
    ) -> PiiColumn:
        """Reject a suggested column: every finding on it is rejected, remembering its
        confidence. 409 ``pii_review_stale`` unless the column is suggested."""
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            column, table = self._load_column(db, system_id, column_id)
            findings = self._findings(db, column.id)
            if self._state(column, findings) != "suggested":
                raise _stale()
            now = self._clock()
            for finding in findings:
                self._decide(db, user, system, finding, "rejected", now)
            self._activity(db, user, system, column, table, "rejected", now)
            db.flush()
            return self._columns(db, system_id, [column.id])[0]

    def add(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        category: str,
    ) -> PiiColumn:
        """Add a present column as PII by hand with ``category`` (no finding). A rejected
        column comes back accepted with its findings. 409 ``pii_review_stale`` for a
        column already PII or waiting in review; 409 ``column_not_present`` for a column
        gone from the source."""
        self._check_category(category)
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            column, table = self._load_column(db, system_id, column_id)
            if column.status != "present":
                raise ApiError(
                    409, "column_not_present", "Only a column present in the source is added."
                )
            findings = self._findings(db, column.id)
            if self._state(column, findings) in ("accepted", "suggested"):
                raise _stale()
            now = self._clock()
            for finding in findings:
                self._decide(db, user, system, finding, "accepted", now)
            self._flag(
                db,
                user,
                system,
                column,
                {
                    "is_sensitive": True,
                    "pii_category": category,
                    "pii_added_by": user.id,
                    "pii_added_at": now,
                },
                now,
            )
            self._scrub_profile(db, column.id)
            self._activity(db, user, system, column, table, "added", now, category=category)
            db.flush()
            return self._columns(db, system_id, [column.id])[0]

    def remove(
        self, user: User, workspace_id: uuid.UUID, system_id: uuid.UUID, column_id: uuid.UUID
    ) -> PiiRemoval:
        """Remove a column's PII: every finding on it is rejected and the sensitive flag
        and category are cleared (however they were set). Returns the column, now
        rejected, or no column for a hand-only one, which leaves the folder. 409
        ``pii_review_stale`` unless the column is PII; 409 ``pii_column_hashed`` while its
        staging mapping is hashed."""
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            column, table = self._load_column(db, system_id, column_id)
            if column.status != "present":
                raise ApiError(
                    409,
                    "column_not_present",
                    "The column is gone from the source: its PII decision stays.",
                )
            findings = self._findings(db, column.id)
            if self._state(column, findings) != "accepted":
                raise _stale()
            hashed = self._hashed(db, [column.id]).get(column.id)
            if hashed is not None:
                raise ApiError(
                    409,
                    "pii_column_hashed",
                    f"The staging mapping {hashed} hashes this column. Switch it back to "
                    "direct in Mappings first.",
                    {"mapping": hashed},
                )
            now = self._clock()
            for finding in findings:
                self._decide(db, user, system, finding, "rejected", now, removed=True)
            self._flag(
                db,
                user,
                system,
                column,
                {
                    "is_sensitive": False,
                    "pii_category": None,
                    "pii_added_by": None,
                    "pii_added_at": None,
                },
                now,
            )
            self._activity(db, user, system, column, table, "removed", now)
            db.flush()
            found = self._columns(db, system_id, [column.id])
            return PiiRemoval(column=found[0] if found else None, table_id=table.id)

    # -- internals ----------------------------------------------------------------------

    def _accept(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        column_id: uuid.UUID,
        category: str,
        expected: str,
    ) -> PiiColumn:
        self._check_category(category)
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        with Session(self._engine) as db, db.begin():
            system = self._system(db, workspace_id, system_id)
            column, table = self._load_column(db, system_id, column_id)
            findings = self._findings(db, column.id)
            if self._state(column, findings) != expected:
                raise _stale()
            now = self._clock()
            for finding in findings:
                self._decide(db, user, system, finding, "accepted", now)
            self._flag(
                db, user, system, column, {"is_sensitive": True, "pii_category": category}, now
            )
            self._scrub_profile(db, column.id)
            self._activity(db, user, system, column, table, "accepted", now, category=category)
            db.flush()
            return self._columns(db, system_id, [column.id])[0]

    @staticmethod
    def _check_category(category: str) -> None:
        if category not in PII_CATEGORIES:
            raise ApiError(
                422,
                "invalid_category",
                f"The PII category must be one of {', '.join(PII_CATEGORIES)}.",
            )

    @staticmethod
    def _state(
        column: SrcColumnRecord, findings: list[PiiFindingRecord]
    ) -> Literal["accepted", "suggested", "rejected"] | None:
        statuses = {f.status for f in findings}
        if column.is_sensitive or "accepted" in statuses:
            return "accepted"
        if "suggested" in statuses:
            return "suggested"
        if "rejected" in statuses:
            return "rejected"
        return None

    def _load_column(
        self, db: Session, system_id: uuid.UUID, column_id: uuid.UUID
    ) -> tuple[SrcColumnRecord, SrcTableRecord]:
        row = db.execute(
            sa.select(SrcColumnRecord, SrcTableRecord)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(
                SrcColumnRecord.id == column_id,
                SrcColumnRecord.status != "deleted",
                SrcDbSchemaRecord.source_system_id == system_id,
            )
            .with_for_update(of=SrcColumnRecord)
        ).first()
        if row is None:
            raise _not_found("Column")
        return row[0], row[1]

    @staticmethod
    def _findings(db: Session, column_id: uuid.UUID) -> list[PiiFindingRecord]:
        return list(
            db.scalars(
                sa.select(PiiFindingRecord)
                .where(PiiFindingRecord.src_column_id == column_id)
                .with_for_update()
            )
        )

    def _decide(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        finding: PiiFindingRecord,
        decision: str,
        now: datetime,
        *,
        removed: bool = False,
    ) -> None:
        if finding.status == decision:
            return
        old_status = finding.status
        finding.status = decision
        finding.decided_by = user.id
        finding.decided_at = now
        finding.removed = removed
        finding.confidence_at_rejection = finding.confidence if decision == "rejected" else None
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

    @staticmethod
    def _activity(
        db: Session,
        user: User,
        system: SourceSystemRecord,
        column: SrcColumnRecord,
        table: SrcTableRecord,
        verb: str,
        now: datetime,
        *,
        category: str | None = None,
    ) -> None:
        details = {"source_system_id": str(system.id), "table": table.name}
        if category is not None:
            details["category"] = category
        record_activity(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            verb=f"pii_column.{verb}",
            object_type="source_column",
            object_id=column.id,
            object_label=column.name,
            details=details,
            at=now,
        )

    def _flag(
        self,
        db: Session,
        user: User,
        system: SourceSystemRecord,
        column: SrcColumnRecord,
        new: dict[str, object],
        now: datetime,
    ) -> None:
        old = {f: getattr(column, f) for f in new}
        changed = [f for f in new if old[f] != new[f]]
        if not changed:
            return
        for field in changed:
            setattr(column, field, new[field])
        column.version += 1

        def plain(value: object) -> object:
            return str(value) if isinstance(value, uuid.UUID | datetime) else value

        record_audit(
            db,
            workspace_id=system.workspace_id,
            actor_id=user.id,
            entity_type="source_column",
            entity_id=column.id,
            old={f: plain(old[f]) for f in changed},
            new={f: plain(new[f]) for f in changed},
            at=now,
        )

    @staticmethod
    def _scrub_profile(db: Session, column_id: uuid.UUID) -> None:
        """A column that becomes PII keeps no stored values: its profile's ``min``,
        ``max`` and top values go; row count, null share and distinct count stay."""
        db.execute(
            sa.update(ColumnProfileRecord)
            .where(ColumnProfileRecord.src_column_id == column_id)
            .values(min=None, max=None, top_values=None)
        )

    def _hashed(self, db: Session, column_ids: Collection[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        if self._hashed_mappings is None or not column_ids:
            return {}
        return self._hashed_mappings(db, column_ids)

    def _columns(
        self,
        db: Session,
        system_id: uuid.UUID,
        column_ids: Collection[uuid.UUID] | None = None,
    ) -> list[PiiColumn]:
        """The Source System's PII-folder columns (or those of ``column_ids`` that still
        belong in it): flagged sensitive or holding a finding."""
        has_finding = sa.exists().where(PiiFindingRecord.src_column_id == SrcColumnRecord.id)
        query = (
            sa.select(SrcColumnRecord, SrcTableRecord, SrcDbSchemaRecord.name)
            .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
            .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
            .where(
                SrcDbSchemaRecord.source_system_id == system_id,
                SrcColumnRecord.status != "deleted",
                sa.or_(SrcColumnRecord.is_sensitive, has_finding),
            )
        )
        if column_ids is not None:
            query = query.where(SrcColumnRecord.id.in_(list(column_ids)))
        rows = db.execute(query).all()
        if not rows:
            return []
        ids = [row[0].id for row in rows]
        findings: dict[uuid.UUID, list[PiiFindingRecord]] = {}
        for finding in db.scalars(
            sa.select(PiiFindingRecord)
            .where(PiiFindingRecord.src_column_id.in_(ids))
            .order_by(PiiFindingRecord.confidence.desc(), PiiFindingRecord.rule)
        ):
            findings.setdefault(finding.src_column_id, []).append(finding)
        profiled: dict[uuid.UUID, datetime] = {
            table_id: at
            for table_id, at in db.execute(
                sa.select(SrcColumnRecord.table_id, sa.func.max(ColumnProfileRecord.profiled_at))
                .join(SrcColumnRecord, SrcColumnRecord.id == ColumnProfileRecord.src_column_id)
                .where(SrcColumnRecord.table_id.in_({row[1].id for row in rows}))
                .group_by(SrcColumnRecord.table_id)
            )
        }
        hashed = self._hashed(db, ids)
        result = []
        for column, table, db_schema in rows:
            own = findings.get(column.id, [])
            state = self._state(column, own)
            if state is None:
                continue
            result.append(
                self._column_view(
                    column,
                    table,
                    db_schema,
                    own,
                    state,
                    hashed=hashed.get(column.id),
                    profiled_at=profiled.get(table.id),
                )
            )
        return result

    @staticmethod
    def _column_view(
        column: SrcColumnRecord,
        table: SrcTableRecord,
        db_schema: str,
        findings: list[PiiFindingRecord],
        state: Literal["accepted", "suggested", "rejected"],
        *,
        hashed: str | None,
        profiled_at: datetime | None,
    ) -> PiiColumn:
        accepted = [f for f in findings if f.status == "accepted"]
        manual = column.pii_added_at is not None or (column.is_sensitive and not accepted)
        decision: Literal["accepted", "added", "rejected", "removed"] | None = None
        decided_by: uuid.UUID | None = None
        decided_at: datetime | None = None
        rejected_before_by: uuid.UUID | None = None
        rejected_before_at: datetime | None = None

        def latest(items: list[PiiFindingRecord]) -> PiiFindingRecord | None:
            decided = [f for f in items if f.decided_at is not None]
            return max(decided, key=lambda f: f.decided_at) if decided else None  # type: ignore[arg-type,return-value]

        if state == "accepted":
            last = latest(accepted)
            added_at = column.pii_added_at
            if added_at is not None and (
                last is None or last.decided_at is None or added_at >= last.decided_at
            ):
                decision, decided_by, decided_at = "added", column.pii_added_by, added_at
            elif last is not None:
                decision, decided_by, decided_at = "accepted", last.decided_by, last.decided_at
        elif state == "rejected":
            last = latest(findings)
            if last is not None:
                decision = "removed" if last.removed else "rejected"
                decided_by, decided_at = last.decided_by, last.decided_at
        else:
            before = latest([f for f in findings if f.confidence_at_rejection is not None])
            if before is not None:
                rejected_before_by, rejected_before_at = before.decided_by, before.decided_at
        category = column.pii_category if state == "accepted" else None
        if category is None and findings:
            category = (accepted or findings)[0].category
        return PiiColumn(
            id=column.id,
            table_id=table.id,
            db_schema=db_schema,
            table=table.name,
            column=column.name,
            data_type=column.current_definition.get("data_type"),
            source_status=column.status,
            state=state,
            category=category,
            is_sensitive=column.is_sensitive,
            rules=[
                PiiRuleMatch(
                    id=f.id,
                    source=f.source,
                    rule=f.rule,
                    category=f.category,
                    confidence=f.confidence,
                    evidence=f.evidence,
                    status=f.status,
                )
                for f in findings
            ],
            manual=manual,
            decision=decision,
            decided_by=decided_by,
            decided_at=decided_at,
            rejected_before_by=rejected_before_by,
            rejected_before_at=rejected_before_at,
            hashed_mapping=hashed,
            table_profiled_at=profiled_at,
        )

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


def _accepted_pii(workspace_id: uuid.UUID) -> sa.Select:
    """The Workspace's accepted PII source columns: an accepted finding, or added as PII by
    hand. A column only flagged sensitive (in the Source Schema) is protected, not PII that
    propagates."""
    accepted = sa.exists().where(
        PiiFindingRecord.src_column_id == SrcColumnRecord.id,
        PiiFindingRecord.status == "accepted",
    )
    return (
        sa.select(SrcColumnRecord.id)
        .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
        .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
        .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
        .where(
            sa.or_(accepted, SrcColumnRecord.pii_added_at.is_not(None)),
            SrcColumnRecord.status != "deleted",
            SourceSystemRecord.workspace_id == workspace_id,
        )
    )


def accepted_pii_column_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """The Workspace's accepted PII source columns (an accepted finding, or added by hand),
    in the caller's session (no permission check); the start of PII propagation through
    lineage, and the columns whose staging mapping may be hashed."""
    return set(db.scalars(_accepted_pii(workspace_id)))


@dataclass(frozen=True)
class AcceptedPiiColumn:
    id: uuid.UUID
    system_code: str
    db_schema: str
    table: str
    column: str
    category: str
    """The column's PII category, else its accepted finding's."""
    rules: list[str]
    """The rules whose findings are accepted; empty for a column added by hand."""
    reviewer_id: uuid.UUID | None
    """Who made the latest accepting decision (or added it by hand)."""
    reviewed_at: datetime | None


def accepted_pii_columns(db: Session, workspace_id: uuid.UUID) -> list[AcceptedPiiColumn]:
    """The Workspace's accepted PII source columns, by system, schema, table and column; in
    the caller's session (no permission check). The PII inventory."""
    rows = db.execute(
        sa.select(
            SrcColumnRecord,
            SrcTableRecord.name,
            SrcDbSchemaRecord.name,
            SourceSystemRecord.code,
        )
        .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
        .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
        .join(SourceSystemRecord, SourceSystemRecord.id == SrcDbSchemaRecord.source_system_id)
        .where(SrcColumnRecord.id.in_(_accepted_pii(workspace_id)))
        .order_by(
            SourceSystemRecord.code,
            SrcDbSchemaRecord.name,
            SrcTableRecord.name,
            SrcColumnRecord.name,
        )
    ).all()
    findings: dict[uuid.UUID, list[PiiFindingRecord]] = {}
    for finding in db.scalars(
        sa.select(PiiFindingRecord)
        .where(
            PiiFindingRecord.src_column_id.in_([row[0].id for row in rows]),
            PiiFindingRecord.status == "accepted",
        )
        .order_by(PiiFindingRecord.rule)
    ):
        findings.setdefault(finding.src_column_id, []).append(finding)
    result = []
    for column, table, db_schema, code in rows:
        own = findings.get(column.id, [])
        decisions = [(f.decided_at, f.decided_by) for f in own if f.decided_at is not None]
        if column.pii_added_at is not None:
            decisions.append((column.pii_added_at, column.pii_added_by))
        reviewed_at, reviewer = max(decisions, key=lambda d: d[0]) if decisions else (None, None)
        category = column.pii_category or (own[0].category if own else None)
        if category is None:
            continue
        result.append(
            AcceptedPiiColumn(
                id=column.id,
                system_code=code,
                db_schema=db_schema,
                table=table,
                column=column.name,
                category=category,
                rules=[f.rule for f in own],
                reviewer_id=reviewer,
                reviewed_at=reviewed_at,
            )
        )
    return result
