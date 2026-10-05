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
