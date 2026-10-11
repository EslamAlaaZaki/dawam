"""Documentation pack module: a Workspace's documentation as one export (spec §6.15, story
128).

Public interface. Other modules import only what is re-exported here:

- ``DocumentationPackService``: any member exports the Source Schemas, the DW Schema per
  Layer, the Core and Mart mappings, the KPIs and a lineage summary, as zipped Markdown
  pages (``zip``), a single self-contained HTML file (``html``) or one combined Markdown file
  (``combined_markdown``, for the file area, which stores no HTML or zip). It is built from
  the other modules' services, so each call authorizes through the workspaces policy and
  an archived Workspace can still export. Metadata only: no row values are ever read.
- ``router``: ``GET /workspaces/{workspace_id}/documentation-pack?format=zip|html`` and
  ``POST /workspaces/{workspace_id}/data-warehouse/files/documentation-pack``.

Owns no tables.
"""

from .api import router
from .service import DocumentationPack, DocumentationPackService

__all__ = ["DocumentationPack", "DocumentationPackService", "router"]
