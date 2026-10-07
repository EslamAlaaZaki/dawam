"""The assistant module's tables. Private: only this module reads or writes them."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dawam.platform.db import Base

TITLE_MAX_LENGTH = 200
MESSAGE_MAX_LENGTH = 8000
ERROR_MAX_LENGTH = 1000


class ConversationRecord(Base):
    """A member's conversation with the assistant in one Workspace: private to its owner
    unless shared with the Workspace's members (spec story 151)."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (
        sa.Index("ix_assistant_conversations_workspace_user", "workspace_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(sa.String(TITLE_MAX_LENGTH))
    shared_with_workspace: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class MessageRecord(Base):
    """One turn of a conversation: what the member asked, or what the assistant answered."""

    __tablename__ = "assistant_messages"
    __table_args__ = (
        sa.CheckConstraint("role IN ('user', 'assistant')", name="role"),
        sa.UniqueConstraint("conversation_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    """1, 2, 3, ... within the conversation: the order of the turns."""
    role: Mapped[str] = mapped_column(sa.String(16))
    content: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))


class RunRecord(Base):
    """One run of the agent loop answering a message: its tools, tokens and duration
    (spec §6.16 "Every run is logged")."""

    __tablename__ = "assistant_runs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'tool_limit', 'cancelled', 'failed')",
            name="status",
        ),
        sa.Index("ix_assistant_runs_conversation_started", "conversation_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE")
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("assistant_messages.id", ondelete="CASCADE")
    )
    answer_message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("assistant_messages.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(sa.String(16))
    cancel_requested: Mapped[bool]
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    """``name``, ``arguments``, ``status`` and ``duration_ms`` of each tool call."""
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    duration_ms: Mapped[int]
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_message: Mapped[str | None] = mapped_column(sa.String(ERROR_MAX_LENGTH))
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
