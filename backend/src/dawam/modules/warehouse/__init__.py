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
- ``MappingExportService``: any member exports the Core and Mart column mappings as a mapping
  sheet (XLSX with a Branches sheet, or CSV); ``files`` saves it to the file area. ``GET
  .../data-warehouse/mapping-sheet`` downloads it.
- ``StagingService``: owners and editors ``generate`` the Staging Layer from the Source Schema
  (software only): one Staging Table per source base table (and per opted-in view), named
  ``stg_<system code>_<database schema>_<table>``, translated types, audit columns, ``direct``
  mappings and lineage; ``StagingResult`` lists what was flagged for review.
  ``POST .../data-warehouse/staging/generate``.
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
from .mapping_export import MappingExportService, MappingSheet
from .mapping_export_api import MappingExportServiceDep, SheetFormat, SheetLayer
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
from .staging_service import StagingFlag, StagingResult, StagingService

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
    "MappingExportService",
    "MappingExportServiceDep",
    "MappingSheet",
    "NamingRules",
    "PlatformProfile",
    "SheetFormat",
    "SheetLayer",
    "StagingFlag",
    "StagingResult",
    "StagingService",
    "TargetPlatform",
    "is_reserved_word",
    "max_identifier_length",
    "platform_profile",
    "router",
]
