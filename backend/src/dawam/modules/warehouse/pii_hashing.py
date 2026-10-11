"""Hashing a confirmed PII column in its staging mapping (spec §6.12, story 138).

An Editor switches a staging column's ``direct`` mapping to ``hashed`` and back. The hashed
expression is a salted SHA-256 of the source column, as lower-case hex, in the target
platform's dialect. The salt is the ``:pii_salt`` bind placeholder, supplied at load time:
DAWAM never asks for it, never stores it and never writes a literal salt into a mapping.
NULL stays NULL, so a missing value does not become the hash of the salt alone.

Only a staging column whose source column has a ``confirmed`` PII finding can be hashed.
The ``value`` lineage edge from the source column is kept, so the column and everything
downstream stay PII-derived; ``propagate_pii`` marks them pseudonymised.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.sources import confirmed_pii_column_ids
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .platforms import TargetPlatform
from .tables import ColumnMappingRecord, DataWarehouseRecord, DwColumnRecord, DwTableRecord

SALT_PLACEHOLDER = ":pii_salt"
"""The bind parameter the loading process fills with the salt; never a value."""

_TEMPLATES: dict[TargetPlatform, tuple[str, str]] = {
    "postgresql": (
        f"encode(sha256(convert_to({SALT_PLACEHOLDER} || CAST(",
        " AS TEXT), 'UTF8')), 'hex')",
    ),
    "sqlserver": (
        f"LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', {SALT_PLACEHOLDER} + CAST(",
        " AS NVARCHAR(MAX))), 2))",
    ),
    "oracle": (
        f"LOWER(RAWTOHEX(STANDARD_HASH({SALT_PLACEHOLDER} || TO_CHAR(",
        "), 'SHA256')))",
    ),
    "snowflake": (f"SHA2({SALT_PLACEHOLDER} || CAST(", " AS VARCHAR), 256)"),
    "bigquery": (f"TO_HEX(SHA256(CONCAT({SALT_PLACEHOLDER}, CAST(", " AS STRING))))"),
}
"""Each platform's salted SHA-256 as ``(before, after)`` the source column expression.

PostgreSQL, SQL Server (``CONCAT_NULL_YIELDS_NULL``), Snowflake and BigQuery propagate a
NULL through the concatenation. Oracle's ``||`` treats NULL as an empty string, so its
template is wrapped in a ``CASE`` by ``hashed_sql``."""


def hashed_sql(platform: TargetPlatform, column: str) -> str:
    """The salted SHA-256 of ``column`` (a source column expression) in ``platform``'s
    dialect, with the salt as the ``:pii_salt`` placeholder."""
    before, after = _TEMPLATES[platform]
    hashed = f"{before}{column}{after}"
    if platform == "oracle":
        return f"CASE WHEN {column} IS NULL THEN NULL ELSE {hashed} END"
    return hashed


def unhashed_sql(platform: TargetPlatform, sql: str) -> str:
    """The source column expression inside a ``hashed_sql`` expression."""
    before, after = _TEMPLATES[platform]
    if platform == "oracle":
        prefix, _, rest = sql.partition(" IS NULL THEN NULL ELSE ")
        column = prefix.removeprefix("CASE WHEN ")
        if rest != f"{before}{column}{after} END":
            raise ValueError(f"not a hashed expression: {sql!r}")
        return column
    if not (sql.startswith(before) and sql.endswith(after)):
        raise ValueError(f"not a hashed expression: {sql!r}")
    return sql[len(before) : len(sql) - len(after)]


@dataclass(frozen=True)
class StagingColumnMapping:
    column_id: uuid.UUID
    column_name: str
    mapping_type: Literal["direct", "hashed"]
    sql_expression: str
    version: int


def _conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "version_conflict",
        "Someone else changed this since you loaded it. Reload and try again.",
        {"current_version": current},
    )


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_mapping", message, {"field": "mapping_type"})


class PiiHashingService:
    """Switches a staging column's mapping between ``direct`` and ``hashed``."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def get_staging_mapping(
        self, user: User, workspace_id: uuid.UUID, column_id: uuid.UUID
    ) -> StagingColumnMapping:
        """A staging column's mapping (any member). 404 ``not_found`` for anything but a
        source-fed staging column of the Workspace."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            column, _, _, record = self._load(db, workspace_id, column_id)
            return _view(column, record)

    def set_staging_mapping(
        self,
        user: User,
        workspace_id: uuid.UUID,
        column_id: uuid.UUID,
        *,
        mapping_type: str,
        version: int,
    ) -> StagingColumnMapping:
        """Make a staging column's mapping ``hashed`` or ``direct`` again (owners and
        editors). 404 ``not_found`` for anything but a source-fed staging column of the
        Workspace; 422 ``invalid_mapping`` for another type, or to hash a column whose
        source column is not confirmed PII; 409 ``version_conflict`` for a stale
        ``version`` (the mapping's)."""
        self._workspaces.authorize(user, Action.EDIT_DW_SCHEMA, workspace_id)
        if mapping_type not in ("direct", "hashed"):
            raise _invalid("A staging column's mapping is direct or hashed.")
        with Session(self._engine) as db, db.begin():
            column, table, warehouse, record = self._load(db, workspace_id, column_id, lock=True)
            if record.version != version:
                raise _conflict(record.version)
            platform: TargetPlatform = warehouse.target_platform  # type: ignore[assignment]
            if record.mapping_type != mapping_type:
                if mapping_type == "hashed":
                    if column.source_column_id not in confirmed_pii_column_ids(db, workspace_id):
                        raise _invalid("Only a confirmed PII column can be hashed.")
                    sql = hashed_sql(platform, record.sql_expression)
                else:
                    sql = unhashed_sql(platform, record.sql_expression)
                now = self._clock()
                old = {"mapping_type": record.mapping_type, "sql_expression": record.sql_expression}
                record.mapping_type = mapping_type
                record.sql_expression = sql
                record.updated_by = user.id
                record.updated_at = now
                record.version += 1
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="column_mapping",
                    entity_id=record.id,
                    old=old,
                    new={"mapping_type": mapping_type, "sql_expression": sql},
                    at=now,
                )
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="column_mapping.updated",
                    object_type="column_mapping",
                    object_id=record.id,
                    object_label=f"{table.name}.{column.name}",
                    details={"layer": table.layer, "table_id": str(table.id)},
                    at=now,
                )
            return _view(column, record)

    @staticmethod
    def _load(
        db: Session, workspace_id: uuid.UUID, column_id: uuid.UUID, *, lock: bool = False
    ) -> tuple[DwColumnRecord, DwTableRecord, DataWarehouseRecord, ColumnMappingRecord]:
        found = db.execute(
            sa.select(DwColumnRecord, DwTableRecord, DataWarehouseRecord)
            .join(DwTableRecord, DwTableRecord.id == DwColumnRecord.table_id)
            .join(DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id)
            .where(
                DwColumnRecord.id == column_id,
                DwTableRecord.layer == "staging",
                DwColumnRecord.source_column_id.is_not(None),
                DataWarehouseRecord.workspace_id == workspace_id,
            )
        ).first()
        query = sa.select(ColumnMappingRecord).where(
            ColumnMappingRecord.dw_column_id == column_id, ColumnMappingRecord.branch_id.is_(None)
        )
        record = db.scalars(query.with_for_update() if lock else query).first() if found else None
        if found is None or record is None:
            raise ApiError(404, "not_found", "Staging column not found.")
        column, table, warehouse = found
        return column, table, warehouse, record


def _view(column: DwColumnRecord, record: ColumnMappingRecord) -> StagingColumnMapping:
    return StagingColumnMapping(
        column_id=column.id,
        column_name=column.name,
        mapping_type=record.mapping_type,  # type: ignore[arg-type]
        sql_expression=record.sql_expression,
        version=record.version,
    )
