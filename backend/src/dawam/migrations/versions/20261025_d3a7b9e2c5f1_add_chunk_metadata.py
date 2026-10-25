"""Add per-passage metadata (type, language, page, sheet, tags) to document chunks, for
filtered search. Existing passages get a document type from their file's MIME type; the
rest is filled when a document is indexed again.

Revision ID: d3a7b9e2c5f1
Revises: a9d4e7b2c615
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "d3a7b9e2c5f1"
down_revision: str | Sequence[str] | None = "a9d4e7b2c615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("document_type", sa.String(16)))
    op.add_column("document_chunks", sa.Column("language", sa.String(8)))
    op.add_column("document_chunks", sa.Column("page", sa.Integer()))
    op.add_column("document_chunks", sa.Column("sheet", sa.String(200)))
    op.add_column(
        "document_chunks",
        sa.Column("tags", ARRAY(sa.Text()), nullable=False, server_default="{}"),
    )
    op.execute(
        "UPDATE document_chunks c SET document_type = CASE f.mime "
        "WHEN 'application/pdf' THEN 'pdf' "
        "WHEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
        "THEN 'docx' "
        "WHEN 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' THEN 'xlsx' "
        "WHEN 'text/markdown' THEN 'md' WHEN 'text/plain' THEN 'txt' "
        "WHEN 'application/sql' THEN 'sql' WHEN 'application/yaml' THEN 'yaml' "
        "WHEN 'text/csv' THEN 'csv' WHEN 'application/json' THEN 'json' END "
        "FROM workspace_files f WHERE f.id = c.file_id"
    )
    op.create_index("ix_document_chunks_tags", "document_chunks", ["tags"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_document_chunks_tags", table_name="document_chunks")
    for column in ("tags", "sheet", "page", "language", "document_type"):
        op.drop_column("document_chunks", column)
