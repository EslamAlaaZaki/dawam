"""The web module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

KIND_MAX_LENGTH = 16
URL_MAX_LENGTH = 500


class WebSettingsRecord(Base):
    """The installation's one row of web access settings (ADR 0008). No row: web search
    and web fetch are off."""

    __tablename__ = "web_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    """Always 1: there is one row."""
    enabled: Mapped[bool]
    """The one switch for web search and web fetch."""
    normal_provider: Mapped[str | None] = mapped_column(sa.String(KIND_MAX_LENGTH))
    """The search provider of Workspaces that are not internal-only (any registered one)."""
    internal_provider: Mapped[str | None] = mapped_column(sa.String(KIND_MAX_LENGTH))
    """The search provider of internal-only Workspaces: an internal one only."""
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WebProviderRecord(Base):
    """A registered search provider, one per kind, with what its last test found."""

    __tablename__ = "web_search_providers"

    kind: Mapped[str] = mapped_column(sa.String(KIND_MAX_LENGTH), primary_key=True)
    base_url: Mapped[str] = mapped_column(sa.String(URL_MAX_LENGTH))
    secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    """The API key sealed with ``SecretBox``; never returned by any API."""
    test_ok: Mapped[bool | None]
    """Null: not tested since it was saved."""
    test_error: Mapped[str | None] = mapped_column(sa.Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
