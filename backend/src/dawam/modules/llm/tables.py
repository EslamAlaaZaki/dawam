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


class SettingsRecord(Base):
    """The installation's one row of AI settings (spec §6.18): which model fills each
    role, the monthly token budget and whether the embedding index is stale."""

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    """Always 1: there is one row."""
    agent_model_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("llm_models.id", ondelete="SET NULL")
    )
    light_model_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("llm_models.id", ondelete="SET NULL")
    )
    embedding_model_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("llm_models.id", ondelete="SET NULL")
    )
    embedding_dimension: Mapped[int | None]
    """The vector dimension of the assigned embedding model's index."""
    monthly_token_budget: Mapped[int | None] = mapped_column(sa.BigInteger)
    """Tokens per calendar month (UTC) for the whole installation; null: unlimited."""
    reindex_needed: Mapped[bool]
    reindex_reason: Mapped[str | None] = mapped_column(sa.String(64))
    reindex_flagged_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceBudgetRecord(Base):
    """A Workspace's own monthly token budget, on top of the installation's."""

    __tablename__ = "llm_workspace_budgets"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_token_budget: Mapped[int] = mapped_column(sa.BigInteger)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class UsageRecord(Base):
    """Tokens one model call used, with who and what it was for."""

    __tablename__ = "llm_usage"
    __table_args__ = (sa.Index("ix_llm_usage_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    role: Mapped[str] = mapped_column(sa.String(16))
    model_name: Mapped[str] = mapped_column(sa.String(MODEL_NAME_MAX_LENGTH))
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    prompt_tokens: Mapped[int]
    completion_tokens: Mapped[int]
    estimated: Mapped[bool]
    """True when DAWAM counted (roughly) because the provider reported nothing."""
