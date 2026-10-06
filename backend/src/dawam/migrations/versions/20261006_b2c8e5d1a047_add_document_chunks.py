"""add document chunks: the files module's searchable passages (full-text and pgvector).

Revision ID: b2c8e5d1a047
Revises: a1f4c7e92b35
Create Date: 2026-10-06 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c8e5d1a047"
down_revision: str | Sequence[str] | None = "a1f4c7e92b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class Vector(sa.types.UserDefinedType):
    """pgvector's ``vector``, with no fixed dimension."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        return "vector"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', search_text)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("embedding_model_id", sa.Uuid(), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["workspace_files.id"],
            name=op.f("fk_document_chunks_file_id_workspace_files"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_document_chunks_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
    )
    op.create_index(op.f("ix_document_chunks_file_id"), "document_chunks", ["file_id"])
    op.create_index("ix_document_chunks_workspace_id", "document_chunks", ["workspace_id"])
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
