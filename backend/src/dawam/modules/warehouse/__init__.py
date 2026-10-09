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
- ``StagingService``: owners and editors ``generate`` the Staging Layer from the Source Schema
  (software only): one Staging Table per source base table (and per opted-in view), named
  ``stg_<system code>_<database schema>_<table>``, translated types, audit columns, ``direct``
  mappings and lineage; ``StagingResult`` lists what was flagged for review.
  ``POST .../data-warehouse/staging/generate``. ``sync`` proposes what a new Snapshot changes
  as a ``sync`` Change Set (staging only: new tables and columns created, changed columns
  updated, dropped ones kept and flagged ``source_removed``; a deleted Staging Table keeps a
  Tombstone and is never re-proposed; an overridden field is a conflict item) and alerts the
  owners and editors; ``drop_removed`` proposes owner-only deletes of ``source_removed``
  staging objects nothing reads. ``POST .../data-warehouse/staging/sync`` and
  ``.../staging/drop-removed``.
- ``StagingTableHandler`` / ``StagingColumnHandler``: the ``changesets`` engine's handlers for
  ``staging_table`` and ``staging_column`` items; the composition root registers them.
- ``calendar`` (pure): the generated date dimension (Gregorian, optional Umm al-Qura Hijri and
  fiscal attributes, configurable weekend) and time dimension. ``POST .../tables/generated``
  adds them as ``generated`` tables.
- ``router``: ``GET|POST|PATCH /workspaces/{workspace_id}/data-warehouse``,
  ``GET /data-warehouse/platforms`` and the Core and Mart model editor under
  ``/workspaces/{workspace_id}/data-warehouse/tables`` (tables, and their columns), the
  column mappings under ``.../tables/{id}/mapping`` and the lineage graph at
  ``/workspaces/{workspace_id}/data-warehouse/lineage``.

Owns the ``data_warehouses``, ``dw_tables``, ``dw_columns``, ``table_mappings``,
``column_mappings``, ``lineage_edges`` and ``tombstones`` tables.
"""

from .api import router
from .ddl_api import DdlLayer, DdlServiceDep
from .ddl_service import DdlPackage, DdlService
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
from .staging_sync import StagingColumnHandler, StagingTableHandler

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
    "NamingRules",
    "PlatformProfile",
    "StagingColumnHandler",
    "StagingFlag",
    "StagingResult",
    "StagingService",
    "StagingTableHandler",
    "TargetPlatform",
    "is_reserved_word",
    "max_identifier_length",
    "platform_profile",
    "router",
]
