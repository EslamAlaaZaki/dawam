"""Sources module: Source Systems and their System Codes (spec story 39).

Public interface. Other modules import only what is re-exported here:

- ``SourceSystemService``: create, list, open and edit a Workspace's Source Systems
  (``SourceSystem``, ``SourceSystemPage``). Every call authorizes through the
  workspaces module's policy; changing a System Code is owner-only.
- ``ConnectionService``: owners see, test and save a Source System's live database
  Connection (``Connection``); the password is sealed and never returned.
- ``router``: ``GET|PUT /workspaces/{workspace_id}/systems/{system_id}/connection``,
  ``POST .../connection/test``, ``GET|POST /workspaces/{workspace_id}/systems``,
  ``GET|PATCH /workspaces/{workspace_id}/systems/{system_id}``.

Owns the ``source_systems`` and ``connections`` tables. ``internal/`` holds the Connector
interface and its PostgreSQL implementation, which later tickets (extraction, profiling)
reach through the service API.
"""

from .api import router
from .connection_service import Connection, ConnectionService
from .service import SourceSystem, SourceSystemPage, SourceSystemService

__all__ = [
    "Connection",
    "ConnectionService",
    "SourceSystem",
    "SourceSystemPage",
    "SourceSystemService",
    "router",
]
