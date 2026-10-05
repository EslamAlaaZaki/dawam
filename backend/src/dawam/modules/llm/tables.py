"""The llm module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

NAME_MAX_LENGTH = 100
URL_MAX_LENGTH = 500
MODEL_NAME_MAX_LENGTH = 200


class ProviderRecord(Base):
    """An LLM provider: where requests go and how to authenticate (spec §6.18)."""

    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(NAME_MAX_LENGTH), unique=True)
    adapter: Mapped[str] = mapped_column(sa.String(32))
    base_url: Mapped[str] = mapped_column(sa.String(URL_MAX_LENGTH))
    secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    """The API key sealed with ``SecretBox``; never returned by any API."""
    is_internal: Mapped[bool]
    timeout_seconds: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class ModelRecord(Base):
    """A model served by a provider, with the roles it fills and what the last
    "Test connection" found out about it."""

    __tablename__ = "llm_models"
    __table_args__ = (sa.UniqueConstraint("provider_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("llm_providers.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.String(MODEL_NAME_MAX_LENGTH))
    roles: Mapped[list[str]] = mapped_column(sa.ARRAY(sa.String(16)))
    context_window: Mapped[int | None]
    supports_tools: Mapped[bool | None]
    supports_streaming: Mapped[bool | None]
    supports_json_schema: Mapped[bool | None]
    embedding_dimension: Mapped[int | None]
    test_ok: Mapped[bool | None]
    """Null: not tested since the model or its provider last changed."""
    test_error_code: Mapped[str | None] = mapped_column(sa.String(32))
    test_error: Mapped[str | None] = mapped_column(sa.Text)
    last_tested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
