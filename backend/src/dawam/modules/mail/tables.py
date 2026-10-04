"""The mail module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

SMTP_SETTINGS_ID = 1


class SmtpSettingsRecord(Base):
    """The SMTP server DAWAM sends through: at most one row. No row: SMTP is off."""

    __tablename__ = "smtp_settings"
    __table_args__ = (
        sa.CheckConstraint(f"id = {SMTP_SETTINGS_ID}", name="single_row"),
        sa.CheckConstraint("security IN ('none', 'starttls', 'tls')", name="security"),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    host: Mapped[str] = mapped_column(sa.String(255))
    port: Mapped[int]
    security: Mapped[str] = mapped_column(sa.String(16))
    username: Mapped[str | None] = mapped_column(sa.String(255))
    password_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    """The SMTP password sealed with ``dawam.platform.crypto.SecretBox`` (AES-256-GCM,
    context ``smtp.password``); never the password itself."""
    sender: Mapped[str] = mapped_column(sa.String(320))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )


class UndeliveredLinkRecord(Base):
    """A link DAWAM could not email (no SMTP, or SMTP failed), kept for an admin to copy
    to the recipient until it expires (spec §6.1, "Without SMTP")."""

    __tablename__ = "undelivered_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient: Mapped[str] = mapped_column(sa.String(320))
    subject: Mapped[str] = mapped_column(sa.String(300))
    purpose: Mapped[str] = mapped_column(sa.String(64))
    url_encrypted: Mapped[str] = mapped_column(sa.Text)
    """The link sealed with ``SecretBox`` (context ``mail.undelivered_link``): it grants
    access (a password reset, an invitation), so a database copy alone must not."""
    reason: Mapped[str] = mapped_column(sa.String(32))
    """``smtp_not_configured`` or ``smtp_failed``."""
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
