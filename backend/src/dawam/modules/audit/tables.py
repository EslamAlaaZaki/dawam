"""The audit module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

ENTITY_TYPE_MAX_LENGTH = 64
ENTITY_ID_MAX_LENGTH = 64
VIA_VALUES = (
    "user",
    "ai",
    "regeneration",
    "sync",
    "propagation",
    "import",
    "platform_change",
    "system_code_change",
)


class AuditEntryRecord(Base):
    """One change to a critical entity (spec §7 ``AuditEntry``). Append-only."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        sa.CheckConstraint("via IN (" + ", ".join(f"'{v}'" for v in VIA_VALUES) + ")", name="via"),
        sa.Index(
            "ix_audit_entries_entity", "workspace_id", "entity_type", "entity_id", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    via: Mapped[str] = mapped_column(sa.String(24))
    change_set_id: Mapped[uuid.UUID | None] = mapped_column()
    entity_type: Mapped[str] = mapped_column(sa.String(ENTITY_TYPE_MAX_LENGTH))
    entity_id: Mapped[str] = mapped_column(sa.String(ENTITY_ID_MAX_LENGTH))
    old: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    new: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
