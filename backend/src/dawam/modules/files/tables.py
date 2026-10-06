"""The files module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from dawam.platform.db import Base

PATH_MAX_LENGTH = 255
MIME_MAX_LENGTH = 100
SECTION_MAX_LENGTH = 200


class WorkspaceFileRecord(Base):
    """One file in a file area (spec §6.17). A *document* is an uploaded file of a
    Source System's area that has extracted text; there is no separate document model."""

    __tablename__ = "workspace_files"
    __table_args__ = (
        sa.UniqueConstraint("workspace_id", "owner_kind", "owner_id", "path"),
        sa.CheckConstraint("owner_kind IN ('source_system', 'data_warehouse')", name="owner_kind"),
        sa.CheckConstraint("kind IN ('generated', 'uploaded')", name="kind"),
        sa.CheckConstraint(
            "text_status IN ('none', 'extracted', 'no_text_found')", name="text_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    owner_kind: Mapped[str] = mapped_column(sa.String(16))
    """``source_system`` or ``data_warehouse``: whose file area the file is in."""
    owner_id: Mapped[uuid.UUID] = mapped_column()
    """The Source System or Data Warehouse (no foreign key: it depends on ``owner_kind``)."""
    path: Mapped[str] = mapped_column(sa.String(PATH_MAX_LENGTH))
    """The file's name in its area. Never used as a location on disk."""
    kind: Mapped[str] = mapped_column(sa.String(16))
    mime: Mapped[str] = mapped_column(sa.String(MIME_MAX_LENGTH))
    """The type found by sniffing the content, never the one the client claimed."""
    storage_key: Mapped[str] = mapped_column(sa.String(64))
    """The random key of the bytes in the storage backend."""
    size: Mapped[int] = mapped_column(sa.BigInteger())
    extracted_text: Mapped[str | None] = mapped_column(sa.Text())
    text_status: Mapped[str] = mapped_column(sa.String(16))
    """``none`` (nothing to extract, e.g. an image), ``extracted`` or ``no_text_found``
    (e.g. a scanned PDF)."""
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class Vector(UserDefinedType[list[float]]):
    """pgvector's ``vector`` without a fixed dimension: the embedding model (and so the
    dimension) is the admin's choice and may change. Values are written and compared in
    raw SQL (``CAST(:v AS vector)``), so this type only has to name the column."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


class DocumentChunkRecord(Base):
    """One searchable passage of an uploaded document: its normalised text for full-text
    search and, when the Workspace allows it, its embedding."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        sa.Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
        sa.Index("ix_document_chunks_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspace_files.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int]
    section: Mapped[str] = mapped_column(sa.String(SECTION_MAX_LENGTH))
    content: Mapped[str] = mapped_column(sa.Text())
    search_text: Mapped[str] = mapped_column(sa.Text())
    """``content`` after Arabic normalisation: what full-text search indexes."""
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, sa.Computed("to_tsvector('simple', search_text)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector)
    embedding_model_id: Mapped[uuid.UUID | None] = mapped_column()
    """The model that made ``embedding`` (no foreign key: a model may be deleted, which
    only makes the chunk stale)."""
    embedding_dimension: Mapped[int | None]
