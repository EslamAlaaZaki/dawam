"""Warehouse module: the Data Warehouse's setup (spec §6.9, story 87).

Public interface. Other modules import only what is re-exported here:

- ``DataWarehouseService``: ``get`` (``None`` before setup), ``set_up`` and ``update``
  for a user, and ``settings_of(workspace_id)`` for a module that already authorized
  the caller and needs the platform, Layer schema names and naming rules.
- ``DataWarehouse``, ``LayerSchemas``, ``NamingRules``, ``DateDimension``: its settings.
- ``platform_profile``, ``max_identifier_length``, ``is_reserved_word`` and
  ``PLATFORM_PROFILES``: each target platform's identifier limit and reserved words,
  for generating valid names.
- ``DdlService``: any member exports the DW Schema as a SQL package in the target platform's
  dialect (``DdlPackage``), for one Layer or the whole Data Warehouse, with unknown-member
  inserts and the seed rows of generated tables; ``files`` saves it, plus ``seed_files``
  (CSV and ``INSERT`` script per generated table), to the file area. ``GET
  .../data-warehouse/ddl`` downloads it.
- ``ValidationService``: any member runs ``run_validation`` over every Core and Mart mapping
  (errors: unparsable SQL, GROUP BY gaps; warnings: unmapped columns, a missing integration
  rule, data-type compatibility such as truncation, see ``type_compat``) and reads ``coverage``
  per table, per Layer and for the whole Data Warehouse (branch-aware). ``modeling_progress``
  fills the DW Modeling part of the stage-progress port. ``GET .../data-warehouse/validation``
  and ``GET .../data-warehouse/coverage``.
- ``calendar`` (pure): the generated date dimension (Gregorian, optional Umm al-Qura Hijri and
  fiscal attributes, configurable weekend) and time dimension. ``POST .../tables/generated``
  adds them as ``generated`` tables.
- ``router``: ``GET|POST|PATCH /workspaces/{workspace_id}/data-warehouse``,
  ``GET /data-warehouse/platforms`` and the Core and Mart model editor under
  ``/workspaces/{workspace_id}/data-warehouse/tables`` (tables, and their columns), the
  column mappings under ``.../tables/{id}/mapping`` and the lineage graph at
  ``/workspaces/{workspace_id}/data-warehouse/lineage``.

Owns the ``data_warehouses``, ``dw_tables``, ``dw_columns``, ``table_mappings``,
``column_mappings`` and ``lineage_edges`` tables.
"""

from .api import router
from .ddl_api import DdlLayer, DdlServiceDep
from .ddl_service import DdlPackage, DdlService
from .mapping_service import MappingService
from .platforms import (
    PLATFORM_PROFILES,
    TARGET_PLATFORMS,
    PlatformProfile,
    TargetPlatform,
    is_reserved_word,
    max_identifier_length,
    platform_profile,
)
from .service import DataWarehouse, DataWarehouseService, DateDimension, LayerSchemas, NamingRules
from .validation_service import ValidationReport, ValidationService

__all__ = [
    "PLATFORM_PROFILES",
    "TARGET_PLATFORMS",
    "DataWarehouse",
    "DataWarehouseService",
    "DateDimension",
    "DdlLayer",
    "DdlPackage",
    "DdlService",
    "DdlServiceDep",
    "LayerSchemas",
    "MappingService",
    "NamingRules",
    "PlatformProfile",
    "TargetPlatform",
    "ValidationReport",
    "ValidationService",
    "is_reserved_word",
    "max_identifier_length",
    "platform_profile",
    "router",
]
