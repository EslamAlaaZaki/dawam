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
- ``PiiScanService``: owners and editors start a value-based PII scan of selected tables of a
  Source System with a live Connection (``start_scan`` queues a ``pii_scan`` job; ``run_scan``
  is its handler, which ``dawam.job_handlers`` registers). Sampled values are tested in memory
  against ``dawam.platform.pii_validators`` and discarded; only the match ratio is stored, as
  the evidence of a ``suggested`` finding (confidence at least 0.5).
- ``RenameService``: members list the rename candidates extraction proposes (``RenameCandidate``);
  owners and editors confirm or reject one, or merge a removed object into an added one, so a
  rename keeps its identity (``RenamedObject``); confirmations and merges are audited.
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
  ``GET|POST /workspaces/{workspace_id}/systems``,
  ``GET|PATCH /workspaces/{workspace_id}/systems/{system_id}``.

Owns the ``source_systems`` and ``connections`` tables, the Source Objects
(``src_db_schemas``, ``src_tables``, ``src_columns``, ``src_routines``) and the Snapshots
(``snapshots``, ``snapshot_*``, ``definition_texts``). ``internal/`` holds the Connector
interface, its PostgreSQL implementation and the Snapshot writer, which other code
reaches only through the service API. Imports ``activity``, ``audit``, ``auth``, ``jobs`` and
``workspaces``.
"""

from .api import router
from .connection_service import Connection, ConnectionService
from .enhancement_service import EnhancementService
from .import_service import ImportResult, ImportStatus, SchemaImportService
from .internal.pii import is_protected
from .pii_scan_service import PII_SCAN_JOB, PiiScanService
from .pii_service import PiiFinding, PiiService
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

__all__ = [
    "EXTRACT_JOB",
    "PII_SCAN_JOB",
    "Connection",
    "ConnectionService",
    "EnhancementService",
    "ImportResult",
    "ImportStatus",
    "PiiFinding",
    "PiiScanService",
    "PiiService",
    "RenameCandidate",
    "RenameService",
    "RenamedObject",
    "SchemaImportService",
    "SearchHit",
    "SnapshotContent",
    "SnapshotService",
    "SnapshotSummary",
    "SourceSchema",
    "SourceSystem",
    "SourceSystemPage",
    "SourceSystemService",
    "is_protected",
    "router",
]
