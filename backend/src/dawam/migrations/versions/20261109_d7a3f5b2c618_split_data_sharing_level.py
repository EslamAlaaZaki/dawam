"""replace the cumulative data_sharing_level with three independent opt-ins (llm module).

Revision ID: d7a3f5b2c618
Revises: c5d8e2a1f347
Create Date: 2026-11-09 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3f5b2c618"
down_revision: str | Sequence[str] | None = "c5d8e2a1f347"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "llm_workspace_settings"


def upgrade() -> None:
    for column in ("share_profiles", "share_documents", "share_samples"):
        op.add_column(
            _TABLE, sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Every Workspace keeps the access its level gave: each level included the ones before.
    op.execute(
        "UPDATE llm_workspace_settings SET "
        "share_profiles = data_sharing_level IN ('profiles', 'documents', 'samples'), "
        "share_documents = data_sharing_level IN ('documents', 'samples'), "
        "share_samples = data_sharing_level = 'samples'"
    )
    # Before this change no document was ever embedded or read by a vision model, so a
    # Workspace that already shared documents has files nobody indexed under that rule:
    # queue one index-from-bytes job for each (the worker picks it up after the deploy).
    op.execute(
        "INSERT INTO jobs (id, workspace_id, type, title, params, status, priority, progress, "
        "log, created_at) "
        "SELECT gen_random_uuid(), s.workspace_id, 'reindex_documents', 'Re-index documents', "
        "jsonb_build_object('workspace_id', s.workspace_id::text, 'extract', true), "
        "'queued', 0, 0, '', now() "
        "FROM llm_workspace_settings s JOIN workspaces w ON w.id = s.workspace_id "
        "WHERE s.share_documents AND w.status = 'active' "
        "AND EXISTS (SELECT 1 FROM workspace_files f WHERE f.workspace_id = s.workspace_id)"
    )
    for column in ("share_profiles", "share_documents", "share_samples"):
        op.alter_column(_TABLE, column, server_default=None)
    op.drop_constraint(op.f("ck_llm_workspace_settings_data_sharing_level"), _TABLE, type_="check")
    op.drop_column(_TABLE, "data_sharing_level")


def downgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "data_sharing_level", sa.String(length=16), nullable=False, server_default="metadata"
        ),
    )
    # Never widen access: the old levels were cumulative, so a row maps to the highest
    # level whose kinds are all on, counting from the bottom (documents alone: metadata).
    op.execute(
        "UPDATE llm_workspace_settings SET data_sharing_level = CASE "
        "WHEN share_profiles AND share_documents AND share_samples THEN 'samples' "
        "WHEN share_profiles AND share_documents THEN 'documents' "
        "WHEN share_profiles THEN 'profiles' ELSE 'metadata' END"
    )
    op.alter_column(_TABLE, "data_sharing_level", server_default=None)
    op.create_check_constraint(
        op.f("ck_llm_workspace_settings_data_sharing_level"),
        _TABLE,
        "data_sharing_level IN ('metadata', 'profiles', 'documents', 'samples')",
    )
    for column in ("share_samples", "share_documents", "share_profiles"):
        op.drop_column(_TABLE, column)
