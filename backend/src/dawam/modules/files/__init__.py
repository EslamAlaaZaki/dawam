"""Files module: Workspace file areas and uploaded documents (spec §6.17, stories 64, 67).

Public interface. Other modules import only what is re-exported here:

- ``FileService``: upload a file to a Source System's or the Data Warehouse's file area
  (owners and editors), list an area, download a file or a whole area as a zip (any
  member), and edit a text file or replace a binary one in place (owners and editors).
  Uploads are checked for size and type
  (sniffed against an allow-list, not trusted from the client), stored under a random
  key in the configured storage backend (``dawam.platform.storage``) and have their
  text extracted (``WorkspaceFile.text_status``). A *document* is such a file; there is
  one model. Every call authorizes through the workspaces module's policy.
- ``router``: ``GET|POST /workspaces/{workspace_id}/systems/{system_id}/files``,
  ``GET|POST /workspaces/{workspace_id}/data-warehouse/files``,
  ``GET .../systems/{system_id}/files/download`` and ``.../data-warehouse/files/download``
  (zips), ``GET /workspaces/{workspace_id}/files/{file_id}/download``,
  ``GET|PUT .../files/{file_id}/content`` (text edit), ``PUT .../files/{file_id}/replace``.

Owns the ``workspace_files`` table.
"""

from .api import router
from .service import FileContent, FileService, TextFile, WorkspaceFile, WorkspaceFilePage

__all__ = ["FileContent", "FileService", "TextFile", "WorkspaceFile", "WorkspaceFilePage", "router"]
