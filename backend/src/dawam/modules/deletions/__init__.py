"""Deletions module: the "deleted" list and permanent deletes (spec §7 "Soft deletes").

An owner soft-deletes a Source System (``SourceSystemService.delete``) or a DW table
(``ModelService.delete_table``): only that object is marked ``deleted``, hidden from the
model and the score, and nothing cascades. This module lists them, shows the owner what the
permanent delete takes with it, and performs it.

Public interface. Other modules import only what is re-exported here:

- ``DeletionService``: ``list`` the deleted objects (any member, ``DeletedObjects``);
  ``system_deletion`` / ``purge_system`` (owners only, ``SystemDeletion``): a Source System's
  Source Objects, Snapshots, Staging Tables, staging mappings, documents and files go with
  it, its KPIs linked to DW columns are re-homed to the Data Warehouse and the rest deleted,
  and the branches reading its Staging Tables stay, broken; ``table_deletion`` /
  ``purge_table`` (owners only, ``TableDeletion``) for a DW table, whose Tombstone stays.
  A purge is one transaction; stored file bytes are deleted once it has committed.
- ``router``: ``GET /workspaces/{workspace_id}/deleted``,
  ``GET|DELETE /workspaces/{workspace_id}/deleted/systems/{system_id}`` and
  ``GET|DELETE /workspaces/{workspace_id}/deleted/tables/{dw_table_id}``.

Owns no tables. Imports ``activity``, ``auth``, ``files``, ``kpis``, ``sources``,
``warehouse`` and ``workspaces``; nothing imports it.
"""

from .api import router
from .service import DeletedObjects, DeletionService, SystemDeletion, TableDeletion

__all__ = ["DeletedObjects", "DeletionService", "SystemDeletion", "TableDeletion", "router"]
