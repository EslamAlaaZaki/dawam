"""The auth module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

IP_MAX_LENGTH = 64
USER_AGENT_MAX_LENGTH = 512
EVENT_TYPE_MAX_LENGTH = 64
TARGET_TYPE_MAX_LENGTH = 64


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
    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    """Failed sign-ins since the last successful one (or the last lock); at
    ``DAWAM_LOGIN_MAX_FAILURES`` the account locks and this goes back to 0."""
    locked_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    """Sign-in is refused, even with the right password, until then."""


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


class LoginFailureRecord(Base):
    """A failed sign-in attempt, for any email (with an account or not).

    It lets an email without an account lock exactly as an account would, so locking
    reveals nothing about which emails have accounts.
    """

    __tablename__ = "login_failures"
    __table_args__ = (sa.Index("ix_login_failures_email_failed_at", "email", "failed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(sa.String(320))
    """As typed, trimmed and lower-cased."""
    ip: Mapped[str | None] = mapped_column(sa.String(IP_MAX_LENGTH))
    failed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    password_checked: Mapped[bool]
    """False when the attempt was refused unchecked because the email was locked;
    such attempts never count towards a lock."""


class SecurityEventRecord(Base):
    """A security-relevant action (spec §7 ``SecurityEvent``), written only through
    ``SecurityEventRecorder``. Append-only; never holds a password or token."""

    __tablename__ = "security_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), unique=True)
    """Insertion order, which breaks ties between events with the same ``created_at``
    (and gives a stable cursor for paging the log)."""
    actor_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    """Who did it, if known. Not a foreign key: the event outlives the user row."""
    event_type: Mapped[str] = mapped_column(sa.String(EVENT_TYPE_MAX_LENGTH))
    target_type: Mapped[str | None] = mapped_column(sa.String(TARGET_TYPE_MAX_LENGTH))
    target_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    """The spec's ``metadata`` (a reserved attribute name on SQLAlchemy classes)."""
    ip: Mapped[str | None] = mapped_column(sa.String(IP_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
