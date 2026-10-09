"""The AI's guarded source queries (ADR 0002, spec §6.8, story 68).

``SourceQueryService.run`` is the only way a model-written query reaches a Source System. In
this order, failing closed at every step:

1. the user may run source queries (owners and editors) and the Source System has a live
   Connection and a Snapshot;
2. the name-based PII rules have run on the latest Snapshot (they run on every new one; this
   covers rules added since), so a freshly connected system is not explored unprotected;
3. ``check_query`` parses the SQL against the latest Snapshot. Only ``SafeQuery.sql`` (SQL
   regenerated from the verified tree) is ever sent to the source, never the model's text;
4. the query runs through the Connector (read-only transaction, statement timeout, row cap)
   with the timeout cut to what is left of the run's budget of source-query seconds;
5. columns the guard says to mask, and columns whose values pass a PII validator, are masked
   before anything leaves this module. A value hit also records a ``suggested`` finding
   (rule ``value-at-query``) on the source column(s), so later queries mask it by name;
6. what the model learns about a failure is a message of our own per error code: a database's
   error text can echo a value.

Known limitation: a model can read an unflagged text column piece by piece with
``SUBSTR``/``LEFT``/``RIGHT``/``REVERSE`` so that no single cell passes a validator. The guard
does not block this (it would block most legitimate exploration); the per-run budget, the row
cap and the audit trail of every query limit and expose it.

Every run is audited (``source_query`` entity, channel ``ai``): the model's SQL with
validator-detectable PII redacted, the outcome, column names, row count and duration, never a
result value. The service never logs a value either.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.crypto import DecryptionError, SecretBox
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import VALIDATORS, first_match, match_ratio, redact_text

from .connection_service import PASSWORD_CONTEXT
from .internal.connector import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    ConnectionParams,
    Connector,
    ConnectorError,
    connector_for,
)
from .internal.pii import load_rule_set, scan_columns
from .internal.query_guard import Rejected, cap_rows, check_query
from .internal.query_guard_catalog import load_guard_catalog
from .tables import (
    ConnectionRecord,
    PiiFindingRecord,
    SnapshotColumnRecord,
    SnapshotRecord,
    SourceSystemRecord,
    SrcColumnRecord,
    SrcDbSchemaRecord,
    SrcTableRecord,
)

DEFAULT_BUDGET_SECONDS = 120.0
"""Total source-query seconds one assistant run may spend (spec §6.8)."""
VALUE_AT_QUERY = "value-at-query"
MASK = "[masked]"
MAX_CELL_CHARS = 500
"""A longer text cell is cut before it is tested and before the model sees it."""
MAX_AUDIT_SQL_CHARS = 2000
WEAK_RULES = ("birth_date", "commercial_registration")
"""Rules too common to prove PII on one value; a column is masked when most of it matches."""
WEAK_MATCH_RATIO = 0.8
WEAK_MIN_VALUES = 3
EVIDENCE = "A value returned by an AI query passed the {rule} check; the value is not kept."
AUDIT_ENTITY = "source_query"

_SAFE_MESSAGES = {
    "timeout": "The database did not answer within the statement timeout.",
    "permission_denied": "The database user lacks a needed permission.",
    "invalid_query": "The database could not run that query.",
    "schema_not_allowed": "That is outside the allowed Database Schemas.",
    "read_only": "DAWAM only reads from source databases.",
    "connection_failed": "DAWAM could not reach the database.",
}
_GENERIC_ERROR = "The database could not run that query."


class QueryBudget:
    """The source-query seconds one assistant run may still spend. Mutable: the tool layer
    makes one per run and every query draws on it, failed ones included."""

    def __init__(self, seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        self.total = seconds
        self.spent = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)

    def spend(self, seconds: float) -> None:
        self.spent += max(0.0, seconds)


@dataclass(frozen=True)
class SourceQueryResult:
    """What the model may see of a query: masked columns are all ``[masked]``."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    """The source had more rows than the cap."""
    masked_columns: tuple[str, ...]
    duration_ms: int
    can_write: bool | None
    """The Connection user could change data at its last test (warn the member)."""
    new_findings: int = field(default=0)


class SourceQueryService:
    """Every method authorizes through the workspaces policy first."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        encryption_key: bytes,
        clock: Clock,
        connectors: Callable[[str, ConnectionParams], Connector] | None = None,
        timer: Callable[[], float] = time.monotonic,
        row_limit: int = DEFAULT_ROW_LIMIT,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._box = SecretBox(encryption_key)
        self._clock = clock
        self._connectors = connectors
        self._timer = timer
        self._row_limit = row_limit

    def run(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        sql: str,
        *,
        budget: QueryBudget,
    ) -> SourceQueryResult:
        """Run ``sql`` (the model's text) on the Source System as described above.

        Raises ``ApiError``: 403/404 as the policy says, 404 ``not_found``, 409
        ``connection_missing`` / ``no_snapshot`` / ``budget_exhausted`` / ``credentials``,
        422 ``query_rejected`` (the guard's reason), 502 ``source_error`` (our message)."""
        self._workspaces.authorize(user, Action.RUN_SOURCE_QUERY, workspace_id)
        started = self._timer()
        outcome: dict[str, Any] = {"outcome": "ok"}
        try:
            result = self._run(workspace_id, system_id, sql, budget, started, outcome)
        except ApiError as exc:
            outcome.update(outcome="rejected" if exc.status_code == 422 else "error")
            outcome["error_code"] = exc.code
            raise
        finally:
            elapsed = self._timer() - started
            outcome["duration_ms"] = round(elapsed * 1000)
            budget.spend(elapsed)
            self._audit(user, workspace_id, system_id, sql, outcome)
        return result

    # -- the steps ----------------------------------------------------------------------

    def _run(
        self,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        sql: str,
        budget: QueryBudget,
        started: float,
        outcome: dict[str, Any],
    ) -> SourceQueryResult:
        if budget.remaining <= 0:
            raise ApiError(
                409,
                "budget_exhausted",
                "This request has used up its source-query time. Answer with what you have.",
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
                    "Querying needs a live Connection: an owner adds one first.",
                )
            snapshot_id = db.scalar(
                sa.select(SnapshotRecord.id).where(
                    SnapshotRecord.source_system_id == system_id, SnapshotRecord.is_latest
                )
            )
            if snapshot_id is None:
                raise ApiError(409, "no_snapshot", "Extract the Source System's metadata first.")
            self._name_scan(db, workspace_id, snapshot_id)
            catalog = load_guard_catalog(
                db,
                snapshot_id=snapshot_id,
                database=connection.database,
                allowed_schemas=tuple(connection.allowed_schemas),
            )
            engine_name = connection.engine
            if engine_name == "sqlserver" and connection.can_write is not False:
                raise ApiError(
                    409,
                    "read_only_login_required",
                    "SQL Server has no read-only session, so the assistant may query it only "
                    "when the last connection test showed the login cannot write. An owner "
                    "must connect with a read-only login and test the connection again.",
                )
            params = self._params(connection, budget)
            can_write = connection.can_write
        checked = check_query(sql, engine=engine_name, catalog=catalog)
        if isinstance(checked, Rejected):
            raise ApiError(422, "query_rejected", redact_text(checked.reason))
        connector = (self._connectors or connector_for)(engine_name, params)
        try:
            # SafeQuery.sql, never `sql`, with the database's own row limit on top.
            capped = cap_rows(
                checked.sql, engine=engine_name, catalog=catalog, limit=self._row_limit + 1
            )
            raw = connector.query(capped or checked.sql, limit=self._row_limit)
        except ConnectorError as exc:
            raise _source_error(exc.code) from None
        except Exception:  # a driver's text can echo a value: say nothing of it
            raise _source_error("database_error") from None
        masked = set(checked.masked_ordinals)
        hits: dict[int, str] = {}
        weak: set[int] = set()
        for ordinal in range(len(raw.columns)):
            if ordinal in masked:
                continue
            column = [row[ordinal] for row in raw.rows if ordinal < len(row)]
            if rule := _value_rule(column):
                hits[ordinal] = rule
            elif _mostly_weak(column):
                weak.add(ordinal)
        masked |= set(hits) | weak
        written = self._record_findings(workspace_id, snapshot_id, checked, hits)
        # Names are the model's own aliases; redacted all the same.
        names = tuple(redact_text(str(n)) for n in raw.columns)
        rows = tuple(
            tuple(MASK if i in masked else _shown(v) for i, v in enumerate(row[: len(raw.columns)]))
            for row in raw.rows
        )
        outcome.update(
            row_count=len(rows),
            columns=list(names),
            masked_columns=[names[i] for i in sorted(masked)],
            value_hits=len(hits),
        )
        return SourceQueryResult(
            columns=names,
            rows=rows,
            row_count=len(rows),
            truncated=raw.truncated,
            masked_columns=tuple(names[i] for i in sorted(masked)),
            duration_ms=round((self._timer() - started) * 1000),
            can_write=can_write,
            new_findings=written,
        )

    def _name_scan(self, db: Session, workspace_id: uuid.UUID, snapshot_id: uuid.UUID) -> None:
        """The name rules on the latest Snapshot's columns. Findings that exist are kept as
        they are, so this is a no-op when the rules already ran."""
        columns = [
            (column_id, name)
            for column_id, name in db.execute(
                sa.select(SnapshotColumnRecord.src_column_id, SnapshotColumnRecord.name).where(
                    SnapshotColumnRecord.snapshot_id == snapshot_id
                )
            )
        ]
        scan_columns(
            db,
            snapshot_id=snapshot_id,
            columns=columns,
            at=self._clock(),
            rules=load_rule_set(db, workspace_id),
        )

    def _params(self, connection: ConnectionRecord, budget: QueryBudget) -> ConnectionParams:
        password = None
        if connection.secret_encrypted is not None:
            try:
                password = self._box.decrypt(connection.secret_encrypted, context=PASSWORD_CONTEXT)
            except DecryptionError:
                raise ApiError(
                    409,
                    "credentials",
                    "The stored password does not decrypt; an owner enters it again.",
                ) from None
        options = dict(connection.options)
        configured = options.get("statement_timeout_seconds", DEFAULT_STATEMENT_TIMEOUT_SECONDS)
        if not isinstance(configured, int) or isinstance(configured, bool) or configured < 1:
            configured = DEFAULT_STATEMENT_TIMEOUT_SECONDS
        # The statement may not outlive what is left of the run's budget.
        options["statement_timeout_seconds"] = max(1, min(configured, math.ceil(budget.remaining)))
        return ConnectionParams(
            host=connection.host,
            port=connection.port,
            database=connection.database,
            username=connection.username,
            password=password,
            allowed_schemas=tuple(connection.allowed_schemas),
            options=options,
        )

    def _record_findings(
        self, workspace_id: uuid.UUID, snapshot_id: uuid.UUID, checked: Any, hits: Mapping[int, str]
    ) -> int:
        """A ``suggested`` ``value-at-query`` finding on every source column behind a column
        whose values hit a validator. A column that already has one keeps it, whatever its
        status. Returns how many were new."""
        if not hits:
            return 0
        wanted: dict[tuple[str, str, str], str] = {}
        for ordinal, rule in hits.items():
            for ref in checked.columns[ordinal].sources:
                schema, table, column = ref.split(".", 2)
                wanted.setdefault((schema, table, column), rule)
        if not wanted:
            return 0
        now = self._clock()
        written = 0
        with Session(self._engine) as db, db.begin():
            ids = {
                (schema, table, column): column_id
                for column_id, schema, table, column in db.execute(
                    sa.select(
                        SrcColumnRecord.id,
                        SrcDbSchemaRecord.name,
                        SrcTableRecord.name,
                        SrcColumnRecord.name,
                    )
                    .join(SrcTableRecord, SrcTableRecord.id == SrcColumnRecord.table_id)
                    .join(SrcDbSchemaRecord, SrcDbSchemaRecord.id == SrcTableRecord.db_schema_id)
                    .join(
                        SnapshotColumnRecord,
                        SnapshotColumnRecord.src_column_id == SrcColumnRecord.id,
                    )
                    .where(SnapshotColumnRecord.snapshot_id == snapshot_id)
                )
            }
            for key, rule in wanted.items():
                column_id = ids.get(key)
                if column_id is None:
                    continue
                known = db.scalar(
                    sa.select(PiiFindingRecord.id).where(
                        PiiFindingRecord.src_column_id == column_id,
                        PiiFindingRecord.rule == VALUE_AT_QUERY,
                    )
                )
                if known is not None:
                    continue
                db.add(
                    PiiFindingRecord(
                        id=uuid.uuid4(),
                        src_column_id=column_id,
                        rule=VALUE_AT_QUERY,
                        category=VALIDATORS[rule].category,
                        confidence=VALIDATORS[rule].weight,
                        evidence=EVIDENCE.format(rule=rule),
                        status="suggested",
                        snapshot_id=snapshot_id,
                        detected_at=now,
                    )
                )
                written += 1
        return written

    def _audit(
        self,
        user: User,
        workspace_id: uuid.UUID,
        system_id: uuid.UUID,
        sql: str,
        outcome: Mapping[str, Any],
    ) -> None:
        with Session(self._engine) as db, db.begin():
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type=AUDIT_ENTITY,
                entity_id=system_id,
                old=None,
                new={"sql": redact_text(sql)[:MAX_AUDIT_SQL_CHARS], **outcome},
                at=self._clock(),
                via="ai",
            )


def _source_error(code: str) -> ApiError:
    """An error for the model: our own wording for the code, never the database's."""
    return ApiError(502, "source_error", _SAFE_MESSAGES.get(code, _GENERIC_ERROR))


def _comparable(value: Any) -> Any:
    """``value`` in a form the validators read: a whole number stored as a decimal or float
    is the number, bytes are their text."""
    if isinstance(value, Decimal | float) and not isinstance(value, bool):
        try:
            return int(value) if value == int(value) else str(value)
        except (ValueError, OverflowError):
            return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8", errors="ignore")
    return value


def _value_rule(values: Any) -> str | None:
    """The first strong PII rule any of the column's values passes, whole or embedded in
    text, or ``None``. Only a rule id leaves."""
    for value in values:
        if value is None:
            continue
        value = _comparable(value)
        if isinstance(value, str):
            value = value[:MAX_CELL_CHARS]
        if (rule := first_match(value)) is not None:
            return rule
        text = value if isinstance(value, str) else str(value)
        embedded: list[str] = []
        redact_text(text, found=embedded)
        if embedded:
            return embedded[0]
    return None


def _mostly_weak(values: list[Any]) -> bool:
    """Whether most of the column's values pass a weak rule (dates of birth, commercial
    registration numbers): each alone proves nothing, a whole column of them is PII."""
    shown = [_comparable(v) for v in values if v is not None]
    if len(shown) < WEAK_MIN_VALUES:
        return False
    for rule in WEAK_RULES:
        found = match_ratio(rule, shown)
        if found is not None and found.ratio >= WEAK_MATCH_RATIO:
            return True
    return False


def _shown(value: Any) -> Any:
    """A cell as the model sees it: JSON-safe, cut, and with embedded PII already tested
    (a column that had any is masked whole, so this only shortens)."""
    value = _comparable(value)
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, datetime) or hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "..."
