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
- ``MappingImportService``: an Editor imports a filled-in mapping sheet (``POST
  .../mapping-sheet/import``): a validation report first (unknown tables, columns and branches
  fail and create nothing; Staging rows are ignored), then one Change Set of origin ``import``
  for what differs. It never changes approval states.
- ``ScoreExportService``: any member exports the latest score as a Markdown or XLSX report
  (story 124); ``GET .../data-warehouse/score/report`` downloads it.
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
  ``open_impacts`` (same session rules) turns the open ``broken_reference`` and
  ``removed_input`` errors back into the ``StructuralChange``s propagation fixes them with,
  for "Fix impacts" (renames are found from former names: the audit trail's renames of
  ``RENAMED_ENTITY_TYPES``).
  ``Renames`` / ``rename_in_sql`` / ``sql_reads`` respell or find renamed identifiers in SQL
  text, in place.
- ``read_schema`` / ``describe_columns`` / ``mart_columns_reading`` and ``replace_kpi_edges`` /
  ``clear_kpi_edges`` (in the caller's session, no permission check): the Core and Mart
  tables a KPI's formula SQL and links refer to, and the ``kpi`` lineage edges this module
  stores for them (``DwSchema``, ``SchemaTable``, ``SchemaColumn``; ``SQL_DIALECTS`` maps a
  target platform to its sqlglot dialect). ``read_mapping_state`` (``MappingState``) adds the
  ``value`` edges between Core and Mart columns and which columns a mapping fills, for the
  rule-based KPI suggestions; ``ident`` spells an identifier for a platform (bare when safe).
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
- ``MappingDraftService`` (``resolve``, ``inputs``, ``plan``): the AI mapping drafts behind the
  assistant's ``draft_mappings`` tool (spec §6.9, story 101). ``plan`` checks the model's
  branches, column mappings and lookups as a save would and returns a ``MappingDraftPlan``: one
  ``mapping_draft`` Change Set item per branch (scope ``MAPPING_DRAFT_SCOPE_KIND`` with the
  table ids); ``MappingDraftHandler`` applies one, never overwriting a mapped column.
- ``DwTableHandler`` / ``DwColumnHandler``: the ``changesets`` engine's handlers for Core and
  Mart ``dw_table`` and ``dw_column`` items (creates and updates); the composition root
  registers them.
- ``ScoreService`` (``current``, ``history``; ``recalculate`` for the system): the rule-based DW
  score (spec §6.11) from the checks in ``score_checks`` run by the engine in ``scoring``;
  ``install_score_recalculation`` (the composition root) rescores after every committed design
  change, debounced. ``KpiReader`` (``DesignKpi``) is how the composition root gives it the
  Workspace's KPIs for the DW-wide KPI checks; ``touch_score`` marks a Workspace's KPIs changed.
  ``GET .../data-warehouse/score`` and ``.../score/history``.
- ``ApprovalService`` (``list``, ``move``): the Layer Approval of Core and Mart (spec §6.10,
  stories 107, 120): ``draft``, ``in_review``, ``approved``; Mart only while Core is approved,
  and revoking Core revokes Mart. Any committed change to a revoking field of a Core or Mart
  object (``REVOKING_FIELDS``), whoever made it, returns the Layer to ``draft``, audited with its
  cause; text fields never revoke and staging changes revoke nothing. ``install_approval_clock``
  (the composition root) times those revocations. ``GET .../data-warehouse/approvals`` and
  ``PUT .../approvals/{layer}``, refused (409 ``score_gate_failed``) while the Layer has errors
  or scores below the minimum an owner set.
- ``ScoreGateService``: owners disable score checks with a reason (left out of the score, listed
  apart) and set a minimum score per Core and Mart Layer (``.../score/disabled-checks``,
  ``.../score/gates``); ``verify_approval`` is the gate ``ApprovalService`` asks.
- ``DwChangeService``: owners ``propose_system_code_change`` (the code plus a rename of every
  affected Staging Table and the ``source_system`` audit constant, origin ``system_code_change``)
  and ``propose_platform_change`` (the platform, Staging column types translated again, and every
  Core and Mart mapping and branch transpiled with sqlglot; untranslatable SQL gets a
  ``manual_review`` item, ``ManualNote``; origin ``platform_change``), each as one Change Set
  (``ProposedChange``); modules add planners (``PlatformPlan``, ``manual_item``,
  ``transpile_sql``) for SQL the warehouse cannot read, e.g. KPI formulas. A direct platform
  edit of a Data Warehouse with tables is 409 ``use_change_set``. ``DataWarehouseHandler``,
  ``TranspiledMappingHandler`` and ``ManualReviewHandler`` handle the ``data_warehouse``,
  ``column_mapping_sql`` and ``manual_review`` items; ``staged_system_check`` fills the
  ``sources`` port that sends a staged system's code change there.
- ``router``: ``GET|POST|PATCH /workspaces/{workspace_id}/data-warehouse``,
  ``GET /data-warehouse/platforms`` and the Core and Mart model editor under
  ``/workspaces/{workspace_id}/data-warehouse/tables`` (tables, and their columns), the
  column mappings under ``.../tables/{id}/mapping`` and the lineage graph at
  ``/workspaces/{workspace_id}/data-warehouse/lineage``.

Owns the ``data_warehouses``, ``dw_tables``, ``dw_columns``, ``table_mappings``,
``column_mappings``, ``lineage_edges``, ``tombstones``, ``score_runs``,
``score_check_results``, ``ai_evaluations``, ``layer_approvals``, ``disabled_checks`` and
``score_gates`` tables.
"""

from .api import router
from .approvals import REVOKING as REVOKING_FIELDS
from .approvals import ApprovalService, LayerApproval
from .approvals import install_clock as install_approval_clock
from .change_service import DwChangeService, ProposedChange, staged_system_check
from .ddl import ident
from .ddl_api import DdlLayer, DdlServiceDep
from .ddl_service import DdlPackage, DdlService
from .dw_changes import (
    PLATFORM_KEY,
    DataWarehouseHandler,
    ManualNote,
    ManualReviewHandler,
    PlatformPlan,
    TranspiledMappingHandler,
    manual_item,
)
from .evaluation_service import AiEvaluation, EvaluationService, Finding, FindingItem
from .fix_impacts import RENAMED_ENTITY_TYPES, open_impacts
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
from .mapping_draft import SCOPE_KIND as MAPPING_DRAFT_SCOPE_KIND
from .mapping_draft import (
    DraftBranch,
    DraftColumn,
    DraftNote,
    DraftTable,
    MappingDraftHandler,
    MappingDraftPlan,
    MappingDraftService,
)
from .mapping_export import MappingExportService, MappingSheet
from .mapping_export_api import MappingExportServiceDep, SheetFormat, SheetLayer
from .mapping_import import MappingImportReport, MappingImportResult, MappingImportService
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
    MappingState,
    SchemaColumn,
    SchemaTable,
    clear_kpi_edges,
    describe_columns,
    mart_columns_reading,
    read_mapping_state,
    read_schema,
    replace_kpi_edges,
)
from .score_export import ScoreExportService, ScoreReportFile
from .score_export_api import ScoreExportServiceDep, ScoreReportFormatQuery
from .score_gate import ScoreGateService
from .score_service import KpiReader, Score, ScoreService
from .score_trigger import ScoreScheduler
from .score_trigger import install as install_score_recalculation
from .score_trigger import touch as touch_score
from .score_trigger import uninstall as uninstall_score_recalculation
from .scoring import DesignKpi
from .service import DataWarehouse, DataWarehouseService, DateDimension, LayerSchemas, NamingRules
from .sql_transpile import Transpiled
from .sql_transpile import transpile as transpile_sql
from .staging_service import StagingFlag, StagingResult, StagingService
from .staging_sync import StagingColumnHandler, StagingTableHandler
from .validation_service import ValidationReport, ValidationService

__all__ = [
    "GENERATION_SCOPE",
    "MAPPING_DRAFT_SCOPE_KIND",
    "PLATFORM_KEY",
    "PLATFORM_PROFILES",
    "RENAMED_ENTITY_TYPES",
    "REVOKING_FIELDS",
    "SQL_DIALECTS",
    "TARGET_PLATFORMS",
    "AiEvaluation",
    "ApprovalService",
    "ColumnMappingHandler",
    "DataWarehouse",
    "DataWarehouseHandler",
    "DataWarehouseService",
    "DateDimension",
    "DdlLayer",
    "DdlPackage",
    "DdlService",
    "DdlServiceDep",
    "DesignKpi",
    "DraftBranch",
    "DraftColumn",
    "DraftNote",
    "DraftTable",
    "DwChangeService",
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
    "LayerApproval",
    "LayerSchemas",
    "LineageEdge",
    "LineageNode",
    "ManualNote",
    "ManualReviewHandler",
    "MappingBranchHandler",
    "MappingDraftHandler",
    "MappingDraftPlan",
    "MappingDraftService",
    "MappingExportService",
    "MappingExportServiceDep",
    "MappingImportReport",
    "MappingImportResult",
    "MappingImportService",
    "MappingService",
    "MappingSheet",
    "MappingState",
    "ModelService",
    "NamingRules",
    "PiiHashingService",
    "PiiPropagation",
    "PlatformPlan",
    "PlatformProfile",
    "Propagation",
    "ProposedChange",
    "Renames",
    "SchemaColumn",
    "SchemaTable",
    "Score",
    "ScoreExportService",
    "ScoreExportServiceDep",
    "ScoreGateService",
    "ScoreReportFile",
    "ScoreReportFormatQuery",
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
    "Transpiled",
    "TranspiledMappingHandler",
    "ValidationReport",
    "ValidationService",
    "clear_kpi_edges",
    "describe_columns",
    "ident",
    "install_approval_clock",
    "install_score_recalculation",
    "is_reserved_word",
    "lineage_edges",
    "lineage_nodes",
    "manual_item",
    "mart_columns_reading",
    "max_identifier_length",
    "open_impacts",
    "pii_notes",
    "pii_origins",
    "plan_propagation",
    "platform_profile",
    "propagate_pii",
    "read_mapping_state",
    "read_schema",
    "rename_in_sql",
    "replace_kpi_edges",
    "router",
    "sql_reads",
    "staged_system_check",
    "touch_score",
    "transpile_sql",
    "uninstall_score_recalculation",
]
