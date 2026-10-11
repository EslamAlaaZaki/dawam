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
- ``ValidationService``: any member runs ``run_validation`` over every Core and Mart mapping
  (errors: unparsable SQL, GROUP BY gaps; warnings: unmapped columns, a missing integration
  rule, data-type compatibility such as truncation, see ``type_compat``) and reads ``coverage``
  per table, per Layer and for the whole Data Warehouse (branch-aware). ``modeling_progress``
  fills the DW Modeling part of the stage-progress port. ``GET .../data-warehouse/validation``
  and ``GET .../data-warehouse/coverage``.
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
- ``plan_propagation`` (in the caller's session, no permission check): what already-applied
  ``StructuralChange``s (rename, retype, nullability, removal; a Mart column newly mapped)
  call for downstream through lineage, per the propagation rule table of spec §6.10: Change
  Set items, the impacted Assets (``Impact``) and what the KPIs need (``KpiRename``,
  ``KpiRemoval``, re-pointing pairs), as a ``Propagation``. ``ModelService`` and
  ``MappingService`` report committed structural changes through a ``StructuralChangeHook``.
  ``ColumnMappingHandler`` and ``MappingBranchHandler`` (with ``DwColumnHandler``) are the handlers
  for the ``dw_column``, ``column_mapping`` and ``mapping_branch`` items it proposes.
  ``Renames`` / ``rename_in_sql`` / ``sql_reads`` respell or find renamed identifiers in SQL
  text, in place.
- ``read_schema`` / ``describe_columns`` / ``mart_columns_reading`` and ``replace_kpi_edges`` /
  ``clear_kpi_edges`` (in the caller's session, no permission check): the Core and Mart
  tables a KPI's formula SQL and links refer to, and the ``kpi`` lineage edges this module
  stores for them (``DwSchema``, ``SchemaTable``, ``SchemaColumn``; ``SQL_DIALECTS`` maps a
  target platform to its sqlglot dialect).
- ``propagate_pii`` (in the caller's session, no permission check): the DW columns a set of
  confirmed PII source columns reaches through ``value`` edges and the tables it only steers
  (``PiiPropagation``), and which of those columns are pseudonymised (hashed, or fed by a
  hashed column); ``pii_notes`` reads their handling notes. The ``lineage`` module serves
  them as the PII view.
- ``PiiHashingService``: owners and editors switch a confirmed PII column's staging mapping to
  ``hashed`` (salted SHA-256 with a ``:pii_salt`` placeholder; the salt is never stored) and
  back (spec §6.12, story 138). ``PUT .../data-warehouse/staging/columns/{id}/mapping``.
- ``lineage_edges`` / ``lineage_nodes`` (in the caller's session, no permission check): the
  stored lineage edges within ``depth`` of a node, walked with recursive CTEs and a cycle
  guard, and the DW columns and tables among a set of node ids (``LineageEdge``,
  ``LineageNode``); the ``lineage`` module labels the rest and serves the graph.
- ``calendar`` (pure): the generated date dimension (Gregorian, optional Umm al-Qura Hijri and
  fiscal attributes, configurable weekend) and time dimension. ``POST .../tables/generated``
  adds them as ``generated`` tables.
- ``EvaluationService`` (``review``, ``store``, ``list``, ``propose_change_set``): the advisory AI
  evaluation of a Layer behind the assistant's ``evaluate_dw`` tool (spec §6.11, story 122);
  ``GET .../data-warehouse/evaluations`` and ``POST .../evaluations/{id}/findings/{n}/change-set``.
- ``GenerationService`` (``inputs``, ``plan``): the AI generation of the Core and Mart Layers
  behind the assistant's ``generate_dw_schema`` tool (spec §6.9, story 88). The model names
  existing objects by short opaque handles; DAWAM issues a ``generation_key`` to every table
  and column it creates, keeps the last generated values (``generated``) and the user's
  overrides (``edited_fields``, proposed only as conflicts), flags creates with an unknown or
  missing handle as possible duplicates, never re-proposes what a user deleted (Tombstones) and
  adds the built-in date dimension when there is none. ``GenerationPlan`` holds the items of
  the one Change Set (scope ``GENERATION_SCOPE``); ``GeneratedTable`` / ``GeneratedColumn`` are
  the model's answer.
- ``DwTableHandler`` / ``DwColumnHandler``: the ``changesets`` engine's handlers for Core and
  Mart ``dw_table`` and ``dw_column`` items (creates and updates); the composition root
  registers them.
- ``ScoreService`` (``current``, ``history``; ``recalculate`` for the system): the rule-based DW
  score (spec §6.11) from the checks in ``score_checks`` run by the engine in ``scoring``;
  ``install_score_recalculation`` (the composition root) rescores after every committed design
  change, debounced. ``KpiReader`` (``DesignKpi``) is how the composition root gives it the
  Workspace's KPIs for the DW-wide KPI checks; ``touch_score`` marks a Workspace's KPIs changed.
  ``GET .../data-warehouse/score`` and ``.../score/history``.
- ``router``: ``GET|POST|PATCH /workspaces/{workspace_id}/data-warehouse``,
  ``GET /data-warehouse/platforms`` and the Core and Mart model editor under
  ``/workspaces/{workspace_id}/data-warehouse/tables`` (tables, and their columns), the
  column mappings under ``.../tables/{id}/mapping`` and the lineage graph at
  ``/workspaces/{workspace_id}/data-warehouse/lineage``.

Owns the ``data_warehouses``, ``dw_tables``, ``dw_columns``, ``table_mappings``,
``column_mappings``, ``lineage_edges``, ``tombstones``, ``score_runs`` and
``score_check_results`` and ``ai_evaluations`` tables.
"""

from .api import router
from .ddl_api import DdlLayer, DdlServiceDep
from .ddl_service import DdlPackage, DdlService
from .evaluation_service import AiEvaluation, EvaluationService, Finding, FindingItem
from .generation import SCOPE as GENERATION_SCOPE
from .generation import (
    DwColumnHandler,
    DwTableHandler,
    GeneratedColumn,
    GeneratedTable,
    GenerationNote,
    GenerationPlan,
    GenerationService,
)
from .lineage_graph import LineageEdge, LineageNode, lineage_edges, lineage_nodes
from .lineage_sql import DIALECTS as SQL_DIALECTS
from .lineage_sql import Renames, rename_in_sql, sql_reads
from .mapping_export import MappingExportService, MappingSheet
from .mapping_export_api import MappingExportServiceDep, SheetFormat, SheetLayer
from .mapping_service import MappingService
from .model_service import ModelService
from .pii_hashing import PiiHashingService
from .pii_propagation import PiiPropagation, pii_notes, pii_origins, propagate_pii
from .platforms import (
    PLATFORM_PROFILES,
    TARGET_PLATFORMS,
    PlatformProfile,
    TargetPlatform,
    is_reserved_word,
    max_identifier_length,
    platform_profile,
)
from .propagation import (
    Impact,
    KpiRemoval,
    KpiRename,
    Propagation,
    StructuralChange,
    StructuralChangeHook,
    plan_propagation,
)
from .propagation_handlers import ColumnMappingHandler, MappingBranchHandler
from .schema_reader import (
    DwSchema,
    SchemaColumn,
    SchemaTable,
    clear_kpi_edges,
    describe_columns,
    mart_columns_reading,
    read_schema,
    replace_kpi_edges,
)
from .score_service import KpiReader, Score, ScoreService
from .score_trigger import ScoreScheduler
from .score_trigger import install as install_score_recalculation
from .score_trigger import touch as touch_score
from .score_trigger import uninstall as uninstall_score_recalculation
from .scoring import DesignKpi
from .service import DataWarehouse, DataWarehouseService, DateDimension, LayerSchemas, NamingRules
from .staging_service import StagingFlag, StagingResult, StagingService
from .staging_sync import StagingColumnHandler, StagingTableHandler
from .validation_service import ValidationReport, ValidationService

__all__ = [
    "GENERATION_SCOPE",
    "PLATFORM_PROFILES",
    "SQL_DIALECTS",
    "TARGET_PLATFORMS",
    "AiEvaluation",
    "ColumnMappingHandler",
    "DataWarehouse",
    "DataWarehouseService",
    "DateDimension",
    "DdlLayer",
    "DdlPackage",
    "DdlService",
    "DdlServiceDep",
    "DesignKpi",
    "DwColumnHandler",
    "DwSchema",
    "DwTableHandler",
    "EvaluationService",
    "Finding",
    "FindingItem",
    "GeneratedColumn",
    "GeneratedTable",
    "GenerationNote",
    "GenerationPlan",
    "GenerationService",
    "Impact",
    "KpiReader",
    "KpiRemoval",
    "KpiRename",
    "LayerSchemas",
    "LineageEdge",
    "LineageNode",
    "MappingBranchHandler",
    "MappingExportService",
    "MappingExportServiceDep",
    "MappingService",
    "MappingSheet",
    "ModelService",
    "NamingRules",
    "PiiHashingService",
    "PiiPropagation",
    "PlatformProfile",
    "Propagation",
    "Renames",
    "SchemaColumn",
    "SchemaTable",
    "Score",
    "ScoreScheduler",
    "ScoreService",
    "SheetFormat",
    "SheetLayer",
    "StagingColumnHandler",
    "StagingFlag",
    "StagingResult",
    "StagingService",
    "StagingTableHandler",
    "StructuralChange",
    "StructuralChangeHook",
    "TargetPlatform",
    "ValidationReport",
    "ValidationService",
    "clear_kpi_edges",
    "describe_columns",
    "install_score_recalculation",
    "is_reserved_word",
    "lineage_edges",
    "lineage_nodes",
    "mart_columns_reading",
    "max_identifier_length",
    "pii_notes",
    "pii_origins",
    "plan_propagation",
    "platform_profile",
    "propagate_pii",
    "read_schema",
    "rename_in_sql",
    "replace_kpi_edges",
    "router",
    "sql_reads",
    "touch_score",
    "uninstall_score_recalculation",
]
