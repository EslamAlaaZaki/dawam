"""The notifications module's table. Private: only this module reads or writes it."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

KIND_MAX_LENGTH = 32
MESSAGE_MAX_LENGTH = 500
REF_TYPE_MAX_LENGTH = 64


class NotificationRecord(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    """The Workspace it is about; they go with it when it is deleted."""
    kind: Mapped[str] = mapped_column(sa.String(KIND_MAX_LENGTH))
    message: Mapped[str] = mapped_column(sa.String(MESSAGE_MAX_LENGTH))
    ref_type: Mapped[str | None] = mapped_column(sa.String(REF_TYPE_MAX_LENGTH))
    ref_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
