"""Data catalog suggestions (#626): the ``catalog_suggestions`` table, one row per Source
Object and field the AI suggested, and ``ai_fields`` on ``src_tables`` / ``src_columns``: the
fields whose value an accepted AI suggestion wrote and nobody has changed since.

Revision ID: d4e7a2c9f135
Revises: c5f1a8e3d742
Create Date: 2026-11-17 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e7a2c9f135"
down_revision: str | Sequence[str] | None = "c5f1a8e3d742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("src_tables", "src_columns"):
        op.add_column(
            table,
            sa.Column(
                "ai_fields",
                sa.ARRAY(sa.String(length=16)),
                server_default="{}",
                nullable=False,
            ),
        )
    op.create_table(
        "catalog_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=False),
        sa.Column("src_table_id", sa.Uuid(), nullable=False),
        sa.Column("src_column_id", sa.Uuid(), nullable=True),
        sa.Column("field", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=4000), nullable=False),
        sa.Column("edited_value", sa.String(length=4000), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "field IN ('description', 'classification', 'scd_hint')",
            name=op.f("ck_catalog_suggestions_field"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'expired')",
            name=op.f("ck_catalog_suggestions_status"),
        ),
        sa.CheckConstraint(
            "src_column_id IS NULL OR field = 'description'",
            name=op.f("ck_catalog_suggestions_column_field"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_catalog_suggestions_decided_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name=op.f("fk_catalog_suggestions_job_id_jobs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_catalog_suggestions_source_system_id_source_systems"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_column_id"],
            ["src_columns.id"],
            name=op.f("fk_catalog_suggestions_src_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_table_id"],
            ["src_tables.id"],
            name=op.f("fk_catalog_suggestions_src_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_suggestions")),
    )
    op.create_index(
        op.f("ix_catalog_suggestions_source_system_id"),
        "catalog_suggestions",
        ["source_system_id", "status"],
    )
    op.create_index(
        op.f("ix_catalog_suggestions_src_table_id"), "catalog_suggestions", ["src_table_id"]
    )
    op.create_index(
        op.f("ix_catalog_suggestions_src_column_id"), "catalog_suggestions", ["src_column_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_catalog_suggestions_src_column_id"), table_name="catalog_suggestions")
    op.drop_index(op.f("ix_catalog_suggestions_src_table_id"), table_name="catalog_suggestions")
    op.drop_index(op.f("ix_catalog_suggestions_source_system_id"), table_name="catalog_suggestions")
    op.drop_table("catalog_suggestions")
    for table in ("src_columns", "src_tables"):
        op.drop_column(table, "ai_fields")
