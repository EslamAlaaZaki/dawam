"""The auth module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

IP_MAX_LENGTH = 64
USER_AGENT_MAX_LENGTH = 512


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (sa.CheckConstraint("system_role IN ('admin', 'user')", name="system_role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(320), unique=True)
    """Stored trimmed and lower-cased, so lookups are case-insensitive."""
    display_name: Mapped[str] = mapped_column(sa.String(200))
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    """An argon2id hash in PHC string format (``$argon2id$...``)."""
    system_role: Mapped[str] = mapped_column(sa.String(16))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    """The last successful sign-in; None until the first."""


class SessionRecord(Base):
    """A signed-in browser. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    """When the user signed in."""
    last_seen_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    """The last request on this session (updated at most once a minute); the idle
    timeout counts from here."""
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    """``created_at`` plus the absolute timeout in force at sign-in; the session ends
    then, however active it is."""
    ip: Mapped[str | None] = mapped_column(sa.String(IP_MAX_LENGTH))
    """The client address at sign-in, as uvicorn reports it (a proxy's
    ``X-Forwarded-For`` counts only from ``DAWAM_FORWARDED_ALLOW_IPS``)."""
    user_agent: Mapped[str | None] = mapped_column(sa.String(USER_AGENT_MAX_LENGTH))
    """The ``User-Agent`` at sign-in, cut to its first 512 characters."""
