"""The changesets module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

ORIGINS = (
    "ai",
    "regeneration",
    "sync",
    "propagation",
    "import",
    "platform_change",
    "system_code_change",
)
CHANGE_SET_STATUSES = ("pending", "applied", "partially_applied", "rejected", "superseded")
ITEM_STATUSES = ("pending", "needs_owner", "accepted", "rejected", "stale", "expired")
OPERATIONS = ("create", "update", "delete")
ROLES = ("editor", "owner")

TITLE_MAX_LENGTH = 200
OBJECT_TYPE_MAX_LENGTH = 40
LABEL_MAX_LENGTH = 300
REASON_MAX_LENGTH = 300


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class ChangeSetRecord(Base):
    """A group of proposed changes shown as a diff (spec §7 ``ChangeSet``)."""

    __tablename__ = "change_sets"
    __table_args__ = (
        sa.CheckConstraint(f"origin IN ({_in(ORIGINS)})", name="origin"),
        sa.CheckConstraint(f"status IN ({_in(CHANGE_SET_STATUSES)})", name="status"),
        sa.Index("ix_change_sets_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    origin: Mapped[str] = mapped_column(sa.String(24))
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """What the Change Set is about (e.g. one Source System); a newer one of the same
    origin and scope supersedes a pending one."""
    title: Mapped[str] = mapped_column(sa.String(TITLE_MAX_LENGTH))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(sa.String(24))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    applied_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    applied_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class ChangeSetItemRecord(Base):
    """One proposed change to one object (spec §7 ``ChangeSetItem``)."""

    __tablename__ = "change_set_items"
    __table_args__ = (
        sa.CheckConstraint(f"operation IN ({_in(OPERATIONS)})", name="operation"),
        sa.CheckConstraint(f"required_role IN ({_in(ROLES)})", name="required_role"),
        sa.CheckConstraint(f"status IN ({_in(ITEM_STATUSES)})", name="status"),
        sa.Index("ix_change_set_items_change_set", "change_set_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("change_sets.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column()
    """Order within the Change Set; an item depends only on earlier ones."""
    object_type: Mapped[str] = mapped_column(sa.String(OBJECT_TYPE_MAX_LENGTH))
    object_id: Mapped[uuid.UUID | None] = mapped_column()
    operation: Mapped[str] = mapped_column(sa.String(8))
    label: Mapped[str] = mapped_column(sa.String(LABEL_MAX_LENGTH))
    base_values: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    """The values of the fields the item changes, as they were when it was proposed."""
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    depends_on: Mapped[list[str]] = mapped_column(JSONB)
    required_role: Mapped[str] = mapped_column(sa.String(8))
    is_conflict: Mapped[bool] = mapped_column()
    status: Mapped[str] = mapped_column(sa.String(16))
    status_reason: Mapped[str | None] = mapped_column(sa.String(REASON_MAX_LENGTH))
    """Why an item is stale, expired or rejected, for the review page."""
