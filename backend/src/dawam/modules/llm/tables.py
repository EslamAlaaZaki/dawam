"""The llm module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
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
    """The installation's one row of AI settings (spec §6.18): the monthly token budget.
    Which models a Workspace uses is its owner's choice (``WorkspaceModelRecord``)."""

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    """Always 1: there is one row."""
    monthly_token_budget: Mapped[int | None] = mapped_column(sa.BigInteger)
    """Tokens per calendar month (UTC) for the whole installation; null: unlimited."""
    monthly_cost_budget: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    """Currency budget per calendar month (UTC), in ``cost_budget_currency``; null: unlimited."""
    cost_budget_currency: Mapped[str | None] = mapped_column(sa.String(3))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceBudgetRecord(Base):
    """A Workspace's own monthly token budget, on top of the installation's."""

    __tablename__ = "llm_workspace_budgets"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_token_budget: Mapped[int] = mapped_column(sa.BigInteger)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceCostBudgetRecord(Base):
    """A Workspace's own monthly currency budget, beside its token budget."""

    __tablename__ = "llm_workspace_cost_budgets"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    monthly_cost_budget: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6))
    currency: Mapped[str] = mapped_column(sa.String(3))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceAiSettingsRecord(Base):
    """What an owner chose for a Workspace's AI (spec §6.18, stories 160, 161). No column
    has a database default: the composition root fills the row in when the Workspace is
    created, deciding ``internal_only`` in code from the installation's models."""

    __tablename__ = "llm_workspace_settings"
    __table_args__ = (
        sa.CheckConstraint(
            "data_sharing_level IN ('metadata', 'profiles', 'documents', 'samples')",
            name="data_sharing_level",
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    internal_only: Mapped[bool]
    """True: the gateway refuses every external provider for every model role."""
    data_sharing_level: Mapped[str] = mapped_column(sa.String(16))
    auto_retrieve: Mapped[bool] = mapped_column(server_default=sa.false())
    """On: the first message of a conversation carries the top document passages."""
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceModelRecord(Base):
    """One of a Workspace's Workspace Models (spec §6.18): a model the owner chose for a
    Model Role. Agent and light have one or more rows, one of them the default; embedding
    and vision have at most one row, which is the default."""

    __tablename__ = "llm_workspace_models"
    __table_args__ = (
        sa.CheckConstraint(
            "role IN ('agent', 'light', 'embedding', 'vision', 'rerank')", name="role"
        ),
        sa.CheckConstraint(
            "role NOT IN ('embedding', 'vision', 'rerank') OR is_default", name="single_is_default"
        ),
        sa.Index(
            "uq_llm_workspace_models_default",
            "workspace_id",
            "role",
            unique=True,
            postgresql_where=sa.text("is_default"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    is_default: Mapped[bool]


class RoleFallbackRecord(Base):
    """One model in the installation's ordered fallback list for a Model Role: tried, in
    ``position`` order, when the Workspace's model for the role is ``unavailable``."""

    __tablename__ = "llm_role_fallbacks"
    __table_args__ = (
        sa.CheckConstraint("role IN ('agent', 'light')", name="role"),
        sa.UniqueConstraint("role", "position", name="uq_llm_role_fallbacks_role_position"),
    )

    role: Mapped[str] = mapped_column(sa.String(16), primary_key=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("llm_models.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int]


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
    cache_read_tokens: Mapped[int] = mapped_column(server_default="0")
    """Part of ``prompt_tokens`` served from the provider's prompt cache (budgets count it)."""
    cache_write_tokens: Mapped[int] = mapped_column(server_default="0")


class ModelPriceRecord(Base):
    """What a model costs per million tokens, keyed by the model name usage records carry.
    An admin edits it; a model without a row has an unknown cost."""

    __tablename__ = "llm_model_prices"
    __table_args__ = (
        sa.CheckConstraint(
            "input_per_million >= 0 AND output_per_million >= 0 AND cached_input_per_million >= 0",
            name="non_negative",
        ),
    )

    model_name: Mapped[str] = mapped_column(sa.String(MODEL_NAME_MAX_LENGTH), primary_key=True)
    currency: Mapped[str] = mapped_column(sa.String(3))
    input_per_million: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6))
    output_per_million: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6))
    cached_input_per_million: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class DocumentAiSettingsRecord(Base):
    """The installation's one row of Document AI settings (24.1): the RAG pipeline's knobs
    an admin set (``knob_values``; a missing knob has its code default) and the bounds owners
    override within (``knob_bounds``; a missing knob is bounded by its hard limits)."""

    __tablename__ = "llm_document_ai_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    """Always 1: there is one row."""
    knob_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    knob_bounds: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int]
    """Bumped by every change; a write naming an older version is refused."""
    updated_by: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class WorkspaceDocumentAiRecord(Base):
    """A Workspace's overrides of the Document AI knobs owners may change (24.1). A missing
    knob inherits the installation's value."""

    __tablename__ = "llm_workspace_document_ai"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    overrides: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int]
    """Bumped by every change; a write naming an older version is refused."""
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
