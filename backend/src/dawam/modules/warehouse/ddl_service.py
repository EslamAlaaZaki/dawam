"""The DDL package of the DW Schema (spec §6.9, story 97) and the seed files of the generated
date and time dimensions (story 90b)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from dawam.modules.auth import User
from dawam.platform.errors import ApiError

from .calendar import DATE_KEY, TIME_KEY, date_rows, seed_csv, time_rows
from .ddl import DdlColumn, DdlTable, SeedData, generate_ddl, seed_statements
from .model_service import ModelService, ModelTable
from .service import DataWarehouse, DataWarehouseService, DateDimension

SQL_MIME = "application/sql"
CSV_MIME = "text/csv"
LAYERS = ("staging", "core", "mart")


@dataclass(frozen=True)
class DdlPackage:
    name: str
    mime: str
    data: bytes


def _ddl_table(t: ModelTable) -> DdlTable:
    return DdlTable(
        id=t.id,
        layer=t.layer,
        name=t.name,
        kind=t.kind,
        unknown_member=t.unknown_member,
        columns=[
            DdlColumn(c.name, c.data_type, c.is_nullable, c.role, c.references_table_id)
            for c in t.columns
        ],
    )


def _seeds(tables: list[ModelTable], settings: DateDimension) -> dict[Any, SeedData]:
    """Rows for each generated table: the date dimension (its key column is ``date_key``) and
    the time dimension (``time_key``). A table loads the columns it has, so a column the
    Data Warehouse settings no longer ask for is still filled. Rows follow the current
    settings (range, weekend, fiscal start month)."""
    seeds: dict[Any, SeedData] = {}
    cache: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        if t.kind != "generated":
            continue
        names = [c.name for c in t.columns]
        if DATE_KEY in names:
            rows = cache.setdefault(DATE_KEY, date_rows(settings))
        elif TIME_KEY in names:
            rows = cache.setdefault(TIME_KEY, time_rows())
        else:
            continue
        seeds[t.id] = SeedData([n for n in names if n in rows[0]], rows)
    return seeds


class DdlService:
    """Authorizes through the services it wraps (any member reads)."""

    def __init__(self, warehouses: DataWarehouseService, model: ModelService) -> None:
        self._warehouses = warehouses
        self._model = model

    def _load(
        self, user: User, workspace_id: uuid.UUID, layer: str | None
    ) -> tuple[DataWarehouse, list[ModelTable]]:
        if layer is not None and layer not in LAYERS:
            raise ApiError(422, "invalid_layer", f"The Layer is one of {', '.join(LAYERS)}.")
        warehouse = self._warehouses.get(user, workspace_id)
        if warehouse is None:
            raise ApiError(404, "not_found", "The Data Warehouse is not set up yet.")
        tables = [
            self._model.get_table(user, workspace_id, summary.id)
            for summary in self._model.list_tables(user, workspace_id, layer=layer)
        ]
        return warehouse, tables

    def export(
        self, user: User, workspace_id: uuid.UUID, *, layer: str | None = None
    ) -> DdlPackage:
        """The package of one Layer, or of the whole Data Warehouse when ``layer`` is
        ``None``, including the seed rows of generated tables. 404 ``not_found`` before
        set up."""
        warehouse, tables = self._load(user, workspace_id, layer)
        sql = generate_ddl(
            warehouse.target_platform,
            warehouse.layer_schemas.names(),
            [_ddl_table(t) for t in tables],
            layers=None if layer is None else [layer],
            seeds=_seeds(tables, warehouse.date_dimension),
        )
        return DdlPackage(
            name=f"ddl-{layer or 'data-warehouse'}.sql", mime=SQL_MIME, data=sql.encode()
        )

    def seed_files(
        self, user: User, workspace_id: uuid.UUID, *, layer: str | None = None
    ) -> list[DdlPackage]:
        """The seed files of the generated tables in scope, saved beside the DDL package: per
        table a CSV and a script of platform ``INSERT`` statements."""
        warehouse, tables = self._load(user, workspace_id, layer)
        schemas = warehouse.layer_schemas.names()
        seeds = _seeds(tables, warehouse.date_dimension)
        files: list[DdlPackage] = []
        for t in sorted(tables, key=lambda t: (t.layer, t.name.lower())):
            seed = seeds.get(t.id)
            if seed is None:
                continue
            statements = seed_statements(warehouse.target_platform, schemas, _ddl_table(t), seed)
            header = f"-- DAWAM seed data for {warehouse.target_platform}: {t.layer}.{t.name}"
            script = "\n\n".join([header, *statements]) + "\n"
            files.append(
                DdlPackage(
                    f"seed-{t.name}.csv", CSV_MIME, seed_csv(seed.columns, seed.rows).encode()
                )
            )
            files.append(DdlPackage(f"seed-{t.name}.sql", SQL_MIME, script.encode()))
        return files
