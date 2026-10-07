"""The DDL package of the DW Schema (spec §6.9, story 97)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from dawam.modules.auth import User
from dawam.platform.errors import ApiError

from .ddl import DdlColumn, DdlTable, generate_ddl
from .model_service import ModelService
from .service import DataWarehouseService

SQL_MIME = "application/sql"
LAYERS = ("staging", "core", "mart")


@dataclass(frozen=True)
class DdlPackage:
    name: str
    mime: str
    data: bytes


class DdlService:
    """Authorizes through the services it wraps (any member reads)."""

    def __init__(self, warehouses: DataWarehouseService, model: ModelService) -> None:
        self._warehouses = warehouses
        self._model = model

    def export(
        self, user: User, workspace_id: uuid.UUID, *, layer: str | None = None
    ) -> DdlPackage:
        """The package of one Layer, or of the whole Data Warehouse when ``layer`` is
        ``None``. 404 ``not_found`` before set up."""
        if layer is not None and layer not in LAYERS:
            raise ApiError(422, "invalid_layer", f"The Layer is one of {', '.join(LAYERS)}.")
        warehouse = self._warehouses.get(user, workspace_id)
        if warehouse is None:
            raise ApiError(404, "not_found", "The Data Warehouse is not set up yet.")
        tables = [
            self._model.get_table(user, workspace_id, summary.id)
            for summary in self._model.list_tables(user, workspace_id, layer=layer)
        ]
        sql = generate_ddl(
            warehouse.target_platform,
            warehouse.layer_schemas.names(),
            [
                DdlTable(
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
                for t in tables
            ],
            layers=None if layer is None else [layer],
        )
        return DdlPackage(
            name=f"ddl-{layer or 'data-warehouse'}.sql", mime=SQL_MIME, data=sql.encode()
        )
