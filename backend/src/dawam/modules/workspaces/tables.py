"""The workspaces module's tables. Private: only this module reads or writes them.

Every Workspace has at least one owner (spec §6.2). The service keeps it so, and the
database backs it with deferred constraint triggers (created by the migration that
adds these tables): at commit, a Workspace that exists must have an ``owner`` row in
``workspace_members``, otherwise the transaction fails with a ``check_violation``
naming ``workspace_has_owner``.

The trigger does not serialise writers: two concurrent transactions demoting or
removing two different owners each still see the other one and both commit. So every
path that changes members (add, remove, change role, transfer) must first lock the
Workspace row ``FOR UPDATE``, as ``update`` does.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

NAME_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 4000
DOMAIN_MAX_LENGTH = 200

OWNER_CONSTRAINT = "workspace_has_owner"
"""The constraint name the owner triggers raise with."""


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH))
    description: Mapped[str] = mapped_column(sa.String(DESCRIPTION_MAX_LENGTH))
    domain: Mapped[str] = mapped_column(sa.String(DOMAIN_MAX_LENGTH))
    """The business domain (e.g. "Retail banking"); may be empty."""
    created_by: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    version: Mapped[int] = mapped_column()
    """Starts at 1 and goes up by one on every edit (optimistic concurrency, spec §8.3)."""


class MemberRecord(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')", name="role"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(sa.String(16))
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    added_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
