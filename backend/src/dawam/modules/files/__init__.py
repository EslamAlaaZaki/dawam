"""Files module: Workspace file areas and uploaded documents (spec §6.17, stories 64, 67).

Public interface. Other modules import only what is re-exported here:

- ``FileService``: upload a file to a Source System's file area (owners and editors),
  list the area and download a file (any member). Uploads are checked for size and type
  (sniffed against an allow-list, not trusted from the client), stored under a random
  key in the configured storage backend (``dawam.platform.storage``) and have their
  text extracted (``WorkspaceFile.text_status``). A *document* is such a file; there is
  one model. Every call authorizes through the workspaces module's policy.
- ``router``: ``GET|POST /workspaces/{workspace_id}/systems/{system_id}/files``,
  ``GET /workspaces/{workspace_id}/files/{file_id}/download``.

- ``DocumentSearchService``: indexes uploaded documents (Postgres full-text with Arabic
  normalisation; pgvector embeddings only when the Workspace's ``DocumentAiPolicy`` lets
  documents reach the embedding model), searches them hybrid with cited passages, and
  re-indexes on demand (the ``reindex_documents`` job, ``REINDEX_JOB``).
- ``router``: also ``GET /workspaces/{workspace_id}/documents/search`` and
  ``POST /workspaces/{workspace_id}/documents/reindex``.
- ``LinkService``: owners and editors link a Source System to repositories, Jira issues and
  Confluence pages by URL (``SourceLink``, story 65), and link documents to tables and
  columns (``FileObjectLink``, story 66); any member lists them, and the documents linked
  to a table or column. Editing uses the ``EDIT_SOURCE_SYSTEM`` rule for links and the
  ``UPLOAD_FILE`` rule for document links.
- the ``router`` also serves ``GET|POST /workspaces/{workspace_id}/systems/{system_id}/links``,
  ``PATCH|DELETE .../links/{source_link_id}``,
  ``GET|POST /workspaces/{workspace_id}/files/{file_id}/object-links``,
  ``DELETE .../object-links/{object_type}/{object_id}`` and
  ``GET /workspaces/{workspace_id}/objects/{object_type}/{object_id}/documents``.

Owns the ``workspace_files``, ``document_chunks``, ``source_links`` and
``file_object_links`` tables.
"""

from fastapi import APIRouter

from .api import router as _files_router
from .link_api import router as _links_router
from .link_service import FileObjectLink, LinkService, SourceLink, SourceLinkPage
from .search import (
    REINDEX_JOB,
    DocumentAiPolicy,
    DocumentAiSettings,
    DocumentSearchService,
    EmbeddingModel,
    EmbeddingModels,
    NoDocumentAi,
    Passage,
    RegisteredEmbeddingModels,
)
from .service import FileContent, FileService, WorkspaceFile, WorkspaceFilePage

router = APIRouter()
router.include_router(_files_router)
router.include_router(_links_router)

__all__ = [
    "REINDEX_JOB",
    "DocumentAiPolicy",
    "DocumentAiSettings",
    "DocumentSearchService",
    "EmbeddingModel",
    "EmbeddingModels",
    "FileContent",
    "FileObjectLink",
    "FileService",
    "LinkService",
    "NoDocumentAi",
    "Passage",
    "RegisteredEmbeddingModels",
    "SourceLink",
    "SourceLinkPage",
    "WorkspaceFile",
    "WorkspaceFilePage",
    "router",
]
