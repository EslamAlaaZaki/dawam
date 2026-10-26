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

- ``DocumentSearchService``: indexes uploaded documents (Postgres full-text with Arabic
  normalisation; pgvector embeddings only when the Workspace's ``DocumentAiPolicy`` lets
  documents reach the embedding model), searches them hybrid with cited passages, and
  re-indexes on demand (the ``reindex_documents`` job, ``REINDEX_JOB``). An upload or
  replacement returns at once with ``index_status`` ``queued``; the ``index_document`` job
  (``INDEX_JOB``) extracts the text and indexes it, moving the file through ``extracting``,
  ``ocr``, ``chunking`` and ``embedding`` to ``indexed``, or to ``failed`` with ``index_error``.
- ``DocumentParser`` / ``DoclingParser``: the layout-aware parsing port and its Docling engine;
  PDF, DOCX and XLSX are parsed to Markdown (headings, reading order, tables) before chunking.
- ``OcrEngine`` / ``DoclingOcr``: the OCR port and its Docling engine (RapidOCR, on the CPU
  or an NVIDIA GPU: setting ``document_accelerator``); a PDF's pages with too little text
  are read by OCR (stage ``ocr``). A Workspace's vision model (``VisionModels``,
  ``RegisteredVisionModels``) reads them instead when the ``DocumentAiPolicy`` lets the
  document reach its provider (an internal one always may); otherwise, or when it fails, the
  self-hosted engine does and the file's ``index_note`` says so.
- ``DocumentSearchService.describe_image``: an uploaded image's description (made on indexing
  by the Workspace's vision model, under the same policy rule), for the assistant's tool.
- ``router``: also ``POST /admin/llm/document-ai/reindex`` (admins: every Workspace's
  documents again), ``GET /workspaces/{workspace_id}/documents/search`` and
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

- For the ``deletions`` module (in the caller's session, no permission check):
  ``system_files`` (``SystemFiles``, ``FileRef``) lists what a Source System's permanent
  delete removes, and ``purge_system_files`` removes it, returning the storage keys to delete
  once committed.
- ``FileContentHandler``: the Change Set handler for ``file`` objects (updating a text file's
  content, story 146); the composition root registers it.

Owns the ``workspace_files``, ``document_chunks``, ``source_links`` and
``file_object_links`` tables.
"""

from fastapi import APIRouter

from .api import admin_router as _admin_router
from .api import router as _files_router
from .deletion import FileRef, SystemFiles, purge_system_files, system_files
from .handlers import FileContentHandler
from .internal.ocr import DoclingOcr, OcrEngine
from .internal.parsing import DoclingParser, DocumentParser
from .internal.rerank import NoRerankModels, RegisteredRerankModels, RerankModel, RerankModels
from .link_api import router as _links_router
from .link_service import FileObjectLink, LinkService, SourceLink, SourceLinkPage
from .search import (
    INDEX_JOB,
    REINDEX_JOB,
    DocumentAiPolicy,
    DocumentAiSettings,
    DocumentSearchService,
    EmbeddingModel,
    EmbeddingModels,
    ImageDescription,
    NoDocumentAi,
    Passage,
    PassageFilters,
    RegisteredEmbeddingModels,
    RegisteredLightModels,
    RegisteredVisionModels,
    VisionModel,
    VisionModels,
)
from .service import FileContent, FileService, TextFile, WorkspaceFile, WorkspaceFilePage

router = APIRouter()
router.include_router(_files_router)
router.include_router(_admin_router)
router.include_router(_links_router)

__all__ = [
    "INDEX_JOB",
    "REINDEX_JOB",
    "DoclingOcr",
    "DoclingParser",
    "DocumentAiPolicy",
    "DocumentAiSettings",
    "DocumentParser",
    "DocumentSearchService",
    "EmbeddingModel",
    "EmbeddingModels",
    "FileContent",
    "FileContentHandler",
    "FileObjectLink",
    "FileRef",
    "FileService",
    "ImageDescription",
    "LinkService",
    "NoDocumentAi",
    "NoRerankModels",
    "OcrEngine",
    "Passage",
    "PassageFilters",
    "RegisteredEmbeddingModels",
    "RegisteredLightModels",
    "RegisteredRerankModels",
    "RegisteredVisionModels",
    "RerankModel",
    "RerankModels",
    "SourceLink",
    "SourceLinkPage",
    "SystemFiles",
    "TextFile",
    "VisionModel",
    "VisionModels",
    "WorkspaceFile",
    "WorkspaceFilePage",
    "purge_system_files",
    "router",
    "system_files",
]
