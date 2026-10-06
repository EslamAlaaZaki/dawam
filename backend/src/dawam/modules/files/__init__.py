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

Owns the ``workspace_files`` and ``document_chunks`` tables.
"""

from .api import router
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

__all__ = [
    "REINDEX_JOB",
    "DocumentAiPolicy",
    "DocumentAiSettings",
    "DocumentSearchService",
    "EmbeddingModel",
    "EmbeddingModels",
    "FileContent",
    "FileService",
    "NoDocumentAi",
    "Passage",
    "RegisteredEmbeddingModels",
    "WorkspaceFile",
    "WorkspaceFilePage",
    "router",
]
