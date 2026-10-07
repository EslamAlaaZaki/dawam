"""Sources module: Source Systems and their System Codes (spec story 39).

Public interface. Other modules import only what is re-exported here:

- ``SourceSystemService``: create, list, open and edit a Workspace's Source Systems
  (``SourceSystem``, ``SourceSystemPage``). Every call authorizes through the
  workspaces module's policy; changing a System Code is owner-only.
- ``ConnectionService``: owners see, test and save a Source System's live database
  Connection (``Connection``); the password is sealed and never returned.
- ``SnapshotService``: owners and editors extract metadata from the Connection into a
  Snapshot (``start_extraction`` queues an ``extract`` job; ``run_extraction`` is its
  handler, which ``dawam.job_handlers`` registers); members read Snapshots (``list``,
  ``get``: ``SnapshotSummary``, ``SnapshotContent``) and browse and search the Source
  Schema (``source_schema``, ``search``: ``SourceSchema``, ``SearchHit``).
- ``SchemaImportService``: owners and editors download the Schema Import template, validate
  and upload a filled one (a Snapshot with ``origin = import``) and ask for a live
  Connection; members see which features an imported system lacks (``ImportResult``,
  ``ImportStatus``).
- ``EnhancementService``: owners and editors add descriptions, tags, a sensitivity flag,
  and (tables) a classification and SCD hint to Source Objects; each change is audited
  (``source_table`` / ``source_column`` entities) and appears in the activity feed.
- ``PiiService``: owners and editors review PII findings (``PiiFinding``): list the queue,
  ``confirm`` (sets ``is_sensitive`` and the PII category) or ``dismiss``; decisions are
  audited. Name rules run on every new Snapshot. ``is_protected`` is the one Protected
  Column policy: ``is_sensitive`` or a ``suggested``/``confirmed`` finding.
- ``DataDictionaryService``: any member exports the latest Snapshot with its enhancements as
  an XLSX data dictionary (``DataDictionary``, never values); ``files`` saves it to the file
  area. ``GET .../systems/{system_id}/data-dictionary`` downloads it.
- ``PiiScanService``: owners and editors start a value-based PII scan of selected tables of a
  Source System with a live Connection (``start_scan`` queues a ``pii_scan`` job; ``run_scan``
  is its handler, which ``dawam.job_handlers`` registers). Sampled values are tested in memory
  against ``dawam.platform.pii_validators`` and discarded; only the match ratio is stored, as
  the evidence of a ``suggested`` finding (confidence at least 0.5).
- ``RelationshipService``: owners and editors infer undeclared relationships of a Source System
  (``start_inference`` queues an ``infer_relationships`` job; ``run_inference`` is its handler,
  which ``dawam.job_handlers`` registers). Candidates are scored from name similarity, type
  compatibility, target uniqueness, JOIN conditions parsed with sqlglot from view and routine
  text, and (live Connection and profiling only) value overlap; those at or above the
  threshold (default 0.6) are kept as ``suggested`` ``Relationship`` rows with their evidence.
  Any member lists them; owners and editors ``accept`` or ``reject`` one, audited.
- ``SourceSummaryService``: any member reads a Source System's dashboard (``SourceSummary``: table
  count, documented %, profiled %, relationships and PII found, computed on read) at
  ``GET .../systems/{system_id}/summary``; ``source_analysis`` fills the stage-progress port.
- ``PiiRuleService``: owners add, edit and delete the Workspace's custom PII rules (name
  keywords and/or a regex, a category and a confidence) and switch built-in rules off or on;
  built-in rules are never edited. ``internal.pii.load_rule_set`` feeds them to the name scan
  of every new Snapshot and to value scans; changes are audited (``pii_rule`` entity).
- ``check_query``: the AI source query guard (ADR 0002). Given SQL, the engine and a
  ``GuardCatalog`` of the latest Snapshot (``load_guard_catalog`` builds one), it returns
  ``Rejected(reason)`` or a ``SafeQuery``: fully qualified SQL regenerated from the verified
  parse tree, plus which output columns to mask. Run ``SafeQuery.sql``, never the input.
  ``untraceable_views`` lists the views the guard cannot trace (not queryable).
- ``RenameService``: members list the rename candidates extraction proposes (``RenameCandidate``);
  owners and editors confirm or reject one, or merge a removed object into an added one, so a
  rename keeps its identity (``RenamedObject``); confirmations and merges are audited.
- ``StagingSourceService``: for the warehouse module (it authorizes first), ``read`` the tables
  staging generation covers (base tables, and views opted in with ``include_view_in_staging``)
  and ``assign_placeholders`` (the stable ``placeholder_no`` of non-Latin names).
- ``router``: ``GET|PUT /workspaces/{workspace_id}/systems/{system_id}/connection``,
  ``POST .../connection/test``, ``POST .../systems/{system_id}/extractions``,
  ``GET .../systems/{system_id}/snapshots[/{snapshot_id}]``,
  ``GET .../systems/{system_id}/schema[/search]``,
  ``GET .../systems/{system_id}/import[/template]``, ``POST .../import/validate``,
  ``POST .../import/upload``, ``POST .../import/connection-requests``,
  ``PATCH .../systems/{system_id}/tables/{table_id}[/columns/{column_id}]``,
  ``GET .../systems/{system_id}/pii-findings``,
  ``POST .../systems/{system_id}/pii-findings/{finding_id}/confirm|dismiss``,
  ``POST .../systems/{system_id}/pii-scans``,
  ``POST .../systems/{system_id}/relationship-inference``,
  ``GET .../systems/{system_id}/relationships``,
  ``POST .../systems/{system_id}/relationships/{relationship_id}/accept|reject``,
  ``GET|POST /workspaces/{workspace_id}/pii-rules``, ``PATCH|DELETE .../pii-rules/{rule_id}``,
  ``PATCH .../pii-rules/built-in/{rule_id}``,
  ``GET|POST /workspaces/{workspace_id}/systems``,
  ``GET|PATCH /workspaces/{workspace_id}/systems/{system_id}``.

Owns the ``source_systems`` and ``connections`` tables, the Source Objects
(``src_db_schemas``, ``src_tables``, ``src_columns``, ``src_routines``) and the Snapshots
(``snapshots``, ``snapshot_*``, ``definition_texts``). ``internal/`` holds the Connector
interface, its PostgreSQL implementation and the Snapshot writer, which other code
reaches only through the service API. Imports ``activity``, ``audit``, ``auth``, ``jobs`` and
``workspaces``.
"""

from fastapi import APIRouter

from .api import router as _systems_router
from .connection_service import Connection, ConnectionService
from .dictionary_api import DataDictionaryServiceDep
from .dictionary_service import DataDictionary, DataDictionaryService
from .enhancement_service import EnhancementService
from .import_service import ImportResult, ImportStatus, SchemaImportService
from .internal.pii import is_protected
from .internal.query_guard import (
    GuardCatalog,
    GuardColumn,
    GuardTable,
    OutputColumn,
    Rejected,
    SafeQuery,
    UntraceableView,
    check_query,
    untraceable_views,
)
from .internal.query_guard_catalog import load_guard_catalog
from .pii_rule_api import router as _pii_rules_router
from .pii_rule_service import PiiRuleService
from .pii_scan_service import PII_SCAN_JOB, PiiScanService
from .pii_service import PiiFinding, PiiService
from .profiling_service import PROFILE_JOB, ProfilingService, TableProfile
from .relationship_service import INFER_JOB, Relationship, RelationshipService
from .rename_service import RenameCandidate, RenamedObject, RenameService
from .service import SourceSystem, SourceSystemPage, SourceSystemService
from .snapshot_service import (
    EXTRACT_JOB,
    SearchHit,
    SnapshotContent,
    SnapshotService,
    SnapshotSummary,
    SourceSchema,
)
from .staging_source_service import (
    StagingColumn,
    StagingSourceService,
    StagingSystem,
    StagingTable,
)
from .summary_service import SourceSummary, SourceSummaryService

router = APIRouter()
router.include_router(_systems_router)
router.include_router(_pii_rules_router)

__all__ = [
    "EXTRACT_JOB",
    "INFER_JOB",
    "PII_SCAN_JOB",
    "PROFILE_JOB",
    "Connection",
    "ConnectionService",
    "DataDictionary",
    "DataDictionaryService",
    "DataDictionaryServiceDep",
    "EnhancementService",
    "GuardCatalog",
    "GuardColumn",
    "GuardTable",
    "ImportResult",
    "ImportStatus",
    "OutputColumn",
    "PiiFinding",
    "PiiRuleService",
    "PiiScanService",
    "PiiService",
    "ProfilingService",
    "Rejected",
    "Relationship",
    "RelationshipService",
    "RenameCandidate",
    "RenameService",
    "RenamedObject",
    "SafeQuery",
    "SchemaImportService",
    "SearchHit",
    "SnapshotContent",
    "SnapshotService",
    "SnapshotSummary",
    "SourceSchema",
    "SourceSummary",
    "SourceSummaryService",
    "SourceSystem",
    "SourceSystemPage",
    "SourceSystemService",
    "StagingColumn",
    "StagingSourceService",
    "StagingSystem",
    "StagingTable",
    "TableProfile",
    "UntraceableView",
    "check_query",
    "is_protected",
    "load_guard_catalog",
    "router",
    "untraceable_views",
]
