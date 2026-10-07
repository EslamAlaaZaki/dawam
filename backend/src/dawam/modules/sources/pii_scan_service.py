"""The value-based PII scan (spec §6.12, stories 131, 132).

An owner or editor picks tables of a Source System that has a live Connection
(``start_scan``); a ``pii_scan`` background job (``run_scan`` is its handler) samples up to
N rows per table (default 1 000) and tests each column's values in memory against the
validators (``internal.pii_scan``). Only the match ratio is recorded, as the evidence of a
``suggested`` finding at or above 0.5 confidence; a column that already has a finding for
the rule keeps its status and gains confidence. The values are discarded as soon as the
table is scored and are never logged: the job log holds table names and counts only.

The source is read outside any app-database transaction; each table's findings are written
in one transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.jobs import Job, JobContext, JobRunner, JobService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import ConnectionParams, Connector, ConnectorError, connector_for
from .internal.pii import load_rule_set
from .internal.pii_rules import RuleSet
from .internal.pii_scan import DEFAULT_SAMPLE_SIZE, MAX_SAMPLE_SIZE, ValueFinding, score_column
from .tables import (
    ConnectionRecord,
    PiiFindingRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

PII_SCAN_JOB = "pii_scan"


class PiiScanError(Exception):
    """Why a scan job failed; its message is safe to show."""


class PiiScanService:
    """Every method that acts for a user authorizes through the workspaces policy."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        jobs: JobRunner,
        encryption_key: bytes,
        clock: Clock,
        connectors: Callable[[str, ConnectionParams], Connector] = connector_for,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._jobs = JobService(engine, runner=jobs, clock=clock)
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._connectors = connectors

    def start_scan(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        table_ids: list[uuid.UUID],
        *,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
    ) -> Job:
        """Queue a ``pii_scan`` job over ``table_ids`` (owners and editors). 409
        ``connection_missing`` without a live Connection, 404 for a table that is not a
        present table of the Source System, 422 for an empty selection or a bad sample
        size."""
        self._workspaces.authorize(user, Action.REVIEW_PII, workspace_id)
        wanted = list(dict.fromkeys(table_ids))
        if not wanted:
            raise ApiError(422, "no_tables", "Select at least one table to scan.")
        if not 1 <= sample_size <= MAX_SAMPLE_SIZE:
            raise ApiError(
                422, "invalid_sample_size", f"The sample size is 1 to {MAX_SAMPLE_SIZE} rows."
            )
        with Session(self._engine) as db, db.begin():
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present" or system.workspace_id != workspace_id:
                raise ApiError(404, "not_found", "Source System not found.")
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            if connection is None:
                raise ApiError(
                    409,
                    "connection_missing",
                    "A value-based PII scan needs a live Connection: an owner adds one first.",
                )
            found = set(
                db.scalars(
                    sa.select(SrcTableRecord.id)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(
                        SrcTableRecord.id.in_(wanted),
                        SrcTableRecord.status == "present",
                        SrcDbSchemaRecord.source_system_id == system_id,
                    )
                )
            )
            if found != set(wanted):
                raise ApiError(404, "not_found", "Table not found.")
            return self._jobs.submit(
                workspace_id,
                PII_SCAN_JOB,
                {
                    "source_system_id": str(system_id),
                    "table_ids": [str(t) for t in wanted],
                    "sample_size": sample_size,
                },
                title=f"PII scan: {system.name}",
                created_by=user.id,
                db=db,
            )

    # -- the job ------------------------------------------------------------------------

    def run_scan(self, params: Mapping[str, Any], ctx: JobContext) -> None:
        """The ``pii_scan`` job handler: sample each table, score it, record the ratios."""
        system_id = uuid.UUID(params["source_system_id"])
        table_ids = [uuid.UUID(t) for t in params["table_ids"]]
        sample_size = int(params.get("sample_size", DEFAULT_SAMPLE_SIZE))
        with Session(self._engine) as db:
            connection = db.scalars(
                sa.select(ConnectionRecord).where(ConnectionRecord.source_system_id == system_id)
            ).first()
            system = db.get(SourceSystemRecord, system_id)
            if system is None or system.status != "present" or connection is None:
                raise PiiScanError("The Source System or its Connection no longer exists.")
            connector = self._connectors(
                connection.engine,
                ConnectionParams(
                    host=connection.host,
                    port=connection.port,
                    database=connection.database,
                    username=connection.username,
                    password=self._password(connection),
                    allowed_schemas=tuple(connection.allowed_schemas),
                    options=dict(connection.options),
                ),
            )
            rules = load_rule_set(db, system.workspace_id)
            targets = [
                (table_id, schema_name, table_name)
                for table_id, schema_name, table_name in db.execute(
                    sa.select(SrcTableRecord.id, SrcDbSchemaRecord.name, SrcTableRecord.name)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .where(
                        SrcTableRecord.id.in_(table_ids),
                        SrcTableRecord.status == "present",
                        SrcDbSchemaRecord.source_system_id == system_id,
                    )
                    .order_by(SrcDbSchemaRecord.name, SrcTableRecord.name)
                )
            ]
        ctx.log(f"Scanning {len(targets)} tables, up to {sample_size} rows each.")
        recorded = 0
        for index, (table_id, schema_name, table_name) in enumerate(targets):
            ctx.raise_if_cancelled()
            ctx.progress(int(100 * index / len(targets)))
            try:
                sample = connector.sample(schema_name, table_name, limit=sample_size)
            except ConnectorError as exc:
                raise PiiScanError(exc.message) from None
            found = self._score(sample.columns, sample.rows, rules)
            written = self._record(table_id, found)
            recorded += written
            ctx.log(
                f"{schema_name}.{table_name}: {len(sample.rows)} rows sampled, "
                f"{len(found)} columns matched, {written} findings recorded."
            )
        ctx.log(f"Done: {recorded} findings recorded.")

    # -- internals ----------------------------------------------------------------------

    def _score(
        self, names: tuple[str, ...], rows: tuple[tuple[Any, ...], ...], rules: RuleSet
    ) -> dict[str, ValueFinding]:
        """The finding per column name; the sampled values stay inside this call."""
        found: dict[str, ValueFinding] = {}
        for position, name in enumerate(names):
            finding = score_column(name, (row[position] for row in rows), rules=rules)
            if finding is not None:
                found[name] = finding
        return found

    def _record(self, table_id: uuid.UUID, found: Mapping[str, ValueFinding]) -> int:
        if not found:
            return 0
        now = self._clock()
        written = 0
        with Session(self._engine) as db, db.begin():
            columns = {
                name: column_id
                for column_id, name in db.execute(
                    sa.select(SrcColumnRecord.id, SrcColumnRecord.name).where(
                        SrcColumnRecord.table_id == table_id, SrcColumnRecord.status == "present"
                    )
                )
            }
            for name, finding in found.items():
                column_id = columns.get(name)
                if column_id is not None:
                    written += self._upsert(db, column_id, finding, now)
        return written

    def _upsert(
        self, db: Session, column_id: uuid.UUID, finding: ValueFinding, now: datetime
    ) -> int:
        existing = db.scalars(
            sa.select(PiiFindingRecord)
            .where(
                PiiFindingRecord.src_column_id == column_id, PiiFindingRecord.rule == finding.rule
            )
            .with_for_update()
        ).first()
        if existing is None:
            db.add(
                PiiFindingRecord(
                    id=uuid.uuid4(),
                    src_column_id=column_id,
                    rule=finding.rule,
                    category=finding.category,
                    confidence=finding.confidence,
                    evidence=finding.evidence[:500],
                    status="suggested",
                    snapshot_id=None,
                    detected_at=now,
                )
            )
            return 1
        # A decision is never undone: only the confidence and the evidence move.
        existing.confidence = max(existing.confidence, finding.confidence)
        existing.evidence = finding.evidence[:500]
        return 1

    def _password(self, connection: ConnectionRecord) -> str | None:
        if connection.secret_encrypted is None:
            return None
        try:
            return self._box.decrypt(connection.secret_encrypted, context=PASSWORD_CONTEXT)
        except DecryptionError:
            raise PiiScanError(
                "The stored password does not decrypt with DAWAM_ENCRYPTION_KEY; "
                "an owner enters it again."
            ) from None
