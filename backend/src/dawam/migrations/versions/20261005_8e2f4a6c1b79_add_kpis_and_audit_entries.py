"""add kpis and audit entries: the kpis module's table and the audit module's trail.

Revision ID: 8e2f4a6c1b79
Revises: c4d1e7a2b963
Create Date: 2026-10-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8e2f4a6c1b79"
down_revision: str | Sequence[str] | None = "c4d1e7a2b963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("via", sa.String(length=24), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("old", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "via IN ('user', 'ai', 'regeneration', 'sync', 'propagation', 'import', "
            "'platform_change', 'system_code_change')",
            name=op.f("ck_audit_entries_via"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_entries_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_entries_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entries")),
    )
    op.create_index(
        "ix_audit_entries_entity",
        "audit_entries",
        ["workspace_id", "entity_type", "entity_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "kpis",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("definition", sa.String(length=4000), nullable=False),
        sa.Column("formula_text", sa.String(length=4000), nullable=False),
        sa.Column("formula_sql", sa.String(length=20000), nullable=True),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("aggregation", sa.String(length=50), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("refresh_frequency", sa.String(length=100), nullable=False),
        sa.Column("targets", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("origin IN ('user', 'ai', 'rule')", name=op.f("ck_kpis_origin")),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'approved')", name=op.f("ck_kpis_status")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_kpis_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_kpis_source_system_id_source_systems"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_kpis_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kpis")),
    )
    op.create_index(
        "ix_kpis_workspace_system", "kpis", ["workspace_id", "source_system_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_kpis_workspace_system", table_name="kpis")
    op.drop_table("kpis")
    op.drop_index("ix_audit_entries_entity", table_name="audit_entries")
    op.drop_table("audit_entries")
