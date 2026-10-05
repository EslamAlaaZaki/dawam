"""Sources module: Source Systems and their System Codes (spec story 39).

Public interface. Other modules import only what is re-exported here:

- ``SourceSystemService``: create, list, open and edit a Workspace's Source Systems
  (``SourceSystem``, ``SourceSystemPage``). Every call authorizes through the
  workspaces module's policy; changing a System Code is owner-only.
- ``router``: ``GET|POST /workspaces/{workspace_id}/systems``,
  ``GET|PATCH /workspaces/{workspace_id}/systems/{system_id}``.

Owns the ``source_systems`` table.
"""

from .api import router
from .service import SourceSystem, SourceSystemPage, SourceSystemService

__all__ = ["SourceSystem", "SourceSystemPage", "SourceSystemService", "router"]
