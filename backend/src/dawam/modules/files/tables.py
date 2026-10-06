"""The files module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

PATH_MAX_LENGTH = 255
MIME_MAX_LENGTH = 100


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


TITLE_MAX_LENGTH = 200
URL_MAX_LENGTH = 2000
NOTE_MAX_LENGTH = 2000
LINK_KINDS = ("repo", "jira", "confluence", "other")
OBJECT_TYPES = ("table", "column")


class SourceLinkRecord(Base):
    """A link by URL from a Source System to a repository, Jira issue, Confluence page
    or anything else (spec §6.17, story 65)."""

    __tablename__ = "source_links"
    __table_args__ = (
        sa.CheckConstraint("kind IN ('repo', 'jira', 'confluence', 'other')", name="kind"),
        sa.Index("ix_source_links_system_title", "source_system_id", "title", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("source_systems.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(sa.String(16))
    title: Mapped[str] = mapped_column(sa.String(TITLE_MAX_LENGTH))
    url: Mapped[str] = mapped_column(sa.String(URL_MAX_LENGTH))
    note: Mapped[str] = mapped_column(sa.String(NOTE_MAX_LENGTH))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class FileObjectLinkRecord(Base):
    """A document linked to a table or column, so it shows on that page (story 66)."""

    __tablename__ = "file_object_links"
    __table_args__ = (
        sa.CheckConstraint("object_type IN ('table', 'column')", name="object_type"),
        sa.Index("ix_file_object_links_object", "object_type", "object_id"),
    )

    file_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspace_files.id", ondelete="CASCADE"), primary_key=True
    )
    object_type: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    object_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    """A Source Table or Column (no foreign key: it depends on ``object_type``)."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
