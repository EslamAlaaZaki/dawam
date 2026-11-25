"""The Staging Layer's mappings in one read (spec §6.7, §6.14): every Staging Table with its
columns, each column's ``direct`` / ``hashed`` mapping and the source column it reads.

The staging column's source (``dw_columns.source_column_id``) names a Source Object that the
sources module owns; it is resolved through ``StagingSourceService`` (names, data types and
the Source System), so no query runs per table or per column.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.sources import StagingSourceService, accepted_pii_column_ids
from dawam.modules.workspaces import Action, WorkspaceService

from .tables import (
    DELETED,
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
)

SOURCE_REMOVED = "source_removed"


@dataclass(frozen=True)
class StagingMappingColumn:
    column_id: uuid.UUID
    column_name: str
    data_type: dict[str, Any]
    is_system: bool
    role: str
    status: str
    review_flags: list[dict[str, Any]]
    mapping_type: str
    """``direct``, ``hashed`` or ``system`` (an audit column)."""
    sql_expression: str
    source_column_name: str | None
    source_data_type: str | None
    is_pii: bool = False
    """Its source column is accepted PII, so it can be hashed."""
    mapping_version: int | None = None
    """The mapping's version, for switching it between direct and hashed."""


@dataclass(frozen=True)
class StagingMappingTable:
    table_id: uuid.UUID
    table_name: str
    status: str
    review_flags: list[dict[str, Any]]
    system_id: uuid.UUID | None
    system_name: str | None
    system_code: str | None
    source_db_schema: str | None
    source_table_name: str | None
    columns: list[StagingMappingColumn]


@dataclass(frozen=True)
class StagingMappingTotals:
    tables: int
    columns: int
    direct: int
    hashed: int
    audit: int
    to_review: int
    pii: int = 0


@dataclass(frozen=True)
class StagingMappings:
    tables: list[StagingMappingTable]
    totals: StagingMappingTotals


class StagingMappingsService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        sources: StagingSourceService | None = None,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._sources = sources or StagingSourceService(engine)

    def read(self, user: User, workspace_id: uuid.UUID) -> StagingMappings:
        """The Staging Layer's tables by name, each with its columns by ordinal (any member).
        Empty before the Data Warehouse is set up."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        origins: dict[uuid.UUID, tuple[Any, Any]] = {}
        source_columns: dict[uuid.UUID, tuple[str, str]] = {}
        for system in self._sources.read(workspace_id, include_removed=True):
            for table in system.tables:
                origins[table.id] = (system, table)
                for column in table.columns:
                    source_columns[column.id] = (column.name, column.data_type)
        with Session(self._engine) as db:
            tables = db.scalars(
                sa.select(DwTableRecord)
                .join(
                    DataWarehouseRecord, DataWarehouseRecord.id == DwTableRecord.data_warehouse_id
                )
                .where(
                    DataWarehouseRecord.workspace_id == workspace_id,
                    DwTableRecord.layer == "staging",
                    DwTableRecord.status != DELETED,
                )
                .order_by(DwTableRecord.name, DwTableRecord.id)
            ).all()
            by_table: dict[uuid.UUID, list[StagingMappingColumn]] = {t.id: [] for t in tables}
            rows = db.execute(
                sa.select(DwColumnRecord, ColumnMappingRecord)
                .outerjoin(
                    ColumnMappingRecord,
                    sa.and_(
                        ColumnMappingRecord.dw_column_id == DwColumnRecord.id,
                        ColumnMappingRecord.branch_id.is_(None),
                    ),
                )
                .where(
                    DwColumnRecord.table_id.in_(list(by_table)), DwColumnRecord.status != DELETED
                )
                .order_by(DwColumnRecord.table_id, DwColumnRecord.ordinal, DwColumnRecord.name)
            ).all()
            pii = accepted_pii_column_ids(db, workspace_id)
            for column, mapping in rows:
                source = source_columns.get(column.source_column_id)
                mapping_type = mapping.mapping_type if mapping else "direct"
                by_table[column.table_id].append(
                    StagingMappingColumn(
                        column_id=column.id,
                        column_name=column.name,
                        data_type=column.data_type,
                        is_system=column.is_system,
                        role=column.role,
                        status=column.status,
                        review_flags=list(column.review_flags or []),
                        mapping_type="system" if _is_audit(column) else mapping_type,
                        sql_expression=mapping.sql_expression if mapping else "",
                        source_column_name=source[0] if source else None,
                        source_data_type=source[1] if source else None,
                        is_pii=column.source_column_id in pii,
                        mapping_version=mapping.version if mapping else None,
                    )
                )
            result = []
            for t in tables:
                found = origins.get(t.source_table_id) if t.source_table_id else None
                system, source_table = found if found else (None, None)
                result.append(
                    StagingMappingTable(
                        table_id=t.id,
                        table_name=t.name,
                        status=t.status,
                        review_flags=list(t.review_flags or []),
                        system_id=system.id if system else None,
                        system_name=system.name if system else None,
                        system_code=system.code if system else None,
                        source_db_schema=source_table.db_schema if source_table else None,
                        source_table_name=source_table.name if source_table else None,
                        columns=by_table[t.id],
                    )
                )
        return StagingMappings(result, _totals(result))


def _is_audit(column: DwColumnRecord) -> bool:
    return column.role == "audit" or column.is_system


def _totals(tables: list[StagingMappingTable]) -> StagingMappingTotals:
    columns = [c for t in tables for c in t.columns]
    return StagingMappingTotals(
        tables=len(tables),
        columns=len(columns),
        direct=sum(1 for c in columns if c.mapping_type == "direct"),
        hashed=sum(1 for c in columns if c.mapping_type == "hashed"),
        audit=sum(1 for c in columns if c.mapping_type == "system"),
        to_review=sum(1 for c in columns if c.review_flags or c.status == SOURCE_REMOVED),
        pii=sum(1 for c in columns if c.is_pii),
    )
