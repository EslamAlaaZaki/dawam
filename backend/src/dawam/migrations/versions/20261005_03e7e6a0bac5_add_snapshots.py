"""add snapshots: the sources module's Source Objects, Snapshots and definition texts.

Revision ID: 03e7e6a0bac5
Revises: e7b3a1c94d26
Create Date: 2026-10-05 19:04:26.282358
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "03e7e6a0bac5"
down_revision: str | Sequence[str] | None = "e7b3a1c94d26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "definition_texts",
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_definition_texts")),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_count", sa.Integer(), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("routine_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("origin IN ('connection', 'import')", name=op.f("ck_snapshots_origin")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_snapshots_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_snapshots_source_system_id_source_systems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
    )
    op.create_index(
        op.f("ix_snapshots_source_system_id"), "snapshots", ["source_system_id"], unique=False
    )
    op.create_index(
        "uq_snapshots_latest",
        "snapshots",
        ["source_system_id"],
        unique=True,
        postgresql_where=sa.text("is_latest"),
    )
    op.create_table(
        "src_db_schemas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_system_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "status IN ('present', 'source_removed', 'out_of_scope', 'deleted')",
            name=op.f("ck_src_db_schemas_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_src_db_schemas_source_system_id_source_systems"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_src_db_schemas")),
        sa.UniqueConstraint(
            "source_system_id", "name", name=op.f("uq_src_db_schemas_source_system_id")
        ),
    )
    op.create_table(
        "snapshot_db_schemas",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_db_schema_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_db_schemas_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_db_schema_id"],
            ["src_db_schemas.id"],
            name=op.f("fk_snapshot_db_schemas_src_db_schema_id_src_db_schemas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "src_db_schema_id", name=op.f("pk_snapshot_db_schemas")
        ),
    )
    op.create_table(
        "src_routines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("db_schema_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.CheckConstraint("kind IN ('procedure', 'function')", name=op.f("ck_src_routines_kind")),
        sa.CheckConstraint(
            "status IN ('present', 'source_removed', 'out_of_scope', 'deleted')",
            name=op.f("ck_src_routines_status"),
        ),
        sa.ForeignKeyConstraint(
            ["db_schema_id"],
            ["src_db_schemas.id"],
            name=op.f("fk_src_routines_db_schema_id_src_db_schemas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_src_routines")),
        sa.UniqueConstraint(
            "db_schema_id", "name", "kind", "signature", name=op.f("uq_src_routines_db_schema_id")
        ),
    )
    op.create_table(
        "src_tables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("db_schema_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("current_definition", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('table', 'view')", name=op.f("ck_src_tables_kind")),
        sa.CheckConstraint(
            "status IN ('present', 'source_removed', 'out_of_scope', 'deleted')",
            name=op.f("ck_src_tables_status"),
        ),
        sa.ForeignKeyConstraint(
            ["db_schema_id"],
            ["src_db_schemas.id"],
            name=op.f("fk_src_tables_db_schema_id_src_db_schemas"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_src_tables")),
        sa.UniqueConstraint("db_schema_id", "name", name=op.f("uq_src_tables_db_schema_id")),
    )
    op.create_table(
        "snapshot_constraints",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_table_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("columns", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("ref_table_id", sa.Uuid(), nullable=True),
        sa.Column("ref_db_schema", sa.Text(), nullable=True),
        sa.Column("ref_table", sa.Text(), nullable=True),
        sa.Column("ref_columns", sa.ARRAY(sa.Text()), nullable=False),
        sa.CheckConstraint(
            "type IN ('pk', 'fk', 'unique')", name=op.f("ck_snapshot_constraints_type")
        ),
        sa.ForeignKeyConstraint(
            ["ref_table_id"],
            ["src_tables.id"],
            name=op.f("fk_snapshot_constraints_ref_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_constraints_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_table_id"],
            ["src_tables.id"],
            name=op.f("fk_snapshot_constraints_src_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "src_table_id", "name", name=op.f("pk_snapshot_constraints")
        ),
    )
    op.create_table(
        "snapshot_indexes",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_table_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("columns", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_indexes_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_table_id"],
            ["src_tables.id"],
            name=op.f("fk_snapshot_indexes_src_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "src_table_id", "name", name=op.f("pk_snapshot_indexes")
        ),
    )
    op.create_table(
        "snapshot_routines",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_routine_id", sa.Uuid(), nullable=False),
        sa.Column("db_schema", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["definition_hash"],
            ["definition_texts.hash"],
            name=op.f("fk_snapshot_routines_definition_hash_definition_texts"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_routines_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_routine_id"],
            ["src_routines.id"],
            name=op.f("fk_snapshot_routines_src_routine_id_src_routines"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "src_routine_id", name=op.f("pk_snapshot_routines")),
    )
    op.create_table(
        "snapshot_tables",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_table_id", sa.Uuid(), nullable=False),
        sa.Column("db_schema", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("view_definition_hash", sa.String(length=64), nullable=True),
        sa.Column("row_estimate", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_tables_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_table_id"],
            ["src_tables.id"],
            name=op.f("fk_snapshot_tables_src_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["view_definition_hash"],
            ["definition_texts.hash"],
            name=op.f("fk_snapshot_tables_view_definition_hash_definition_texts"),
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "src_table_id", name=op.f("pk_snapshot_tables")),
    )
    op.create_table(
        "src_columns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("current_definition", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('present', 'source_removed', 'out_of_scope', 'deleted')",
            name=op.f("ck_src_columns_status"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["src_tables.id"],
            name=op.f("fk_src_columns_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_src_columns")),
        sa.UniqueConstraint("table_id", "name", name=op.f("uq_src_columns_table_id")),
    )
    op.create_table(
        "snapshot_columns",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("src_column_id", sa.Uuid(), nullable=False),
        sa.Column("src_table_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.Text(), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("is_pk", sa.Boolean(), nullable=False),
        sa.Column("default", sa.Text(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["snapshots.id"],
            name=op.f("fk_snapshot_columns_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_column_id"],
            ["src_columns.id"],
            name=op.f("fk_snapshot_columns_src_column_id_src_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["src_table_id"],
            ["src_tables.id"],
            name=op.f("fk_snapshot_columns_src_table_id_src_tables"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "src_column_id", name=op.f("pk_snapshot_columns")),
    )


def downgrade() -> None:
    op.drop_table("snapshot_columns")
    op.drop_table("src_columns")
    op.drop_table("snapshot_tables")
    op.drop_table("snapshot_routines")
    op.drop_table("snapshot_indexes")
    op.drop_table("snapshot_constraints")
    op.drop_table("src_tables")
    op.drop_table("src_routines")
    op.drop_table("snapshot_db_schemas")
    op.drop_table("src_db_schemas")
    op.drop_index(
        "uq_snapshots_latest", table_name="snapshots", postgresql_where=sa.text("is_latest")
    )
    op.drop_index(op.f("ix_snapshots_source_system_id"), table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_table("definition_texts")
