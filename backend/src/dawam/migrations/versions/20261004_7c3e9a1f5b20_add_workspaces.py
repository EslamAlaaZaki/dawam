"""add workspaces: the workspaces module's workspaces and workspace_members tables.

Also the deferred constraint triggers that keep every Workspace owned (spec §6.2): at
commit, a Workspace that exists must have at least one ``owner`` member.

Revision ID: 7c3e9a1f5b20
Revises: 57d75041af55
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c3e9a1f5b20"
down_revision: str | Sequence[str] | None = "57d75041af55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_CHECK = """
CREATE FUNCTION workspace_must_have_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target uuid;
BEGIN
    IF TG_TABLE_NAME = 'workspaces' THEN
        target := NEW.id;
    ELSE
        target := OLD.workspace_id;
    END IF;
    IF EXISTS (SELECT 1 FROM workspaces WHERE id = target)
       AND NOT EXISTS (
           SELECT 1 FROM workspace_members WHERE workspace_id = target AND role = 'owner'
       ) THEN
        RAISE EXCEPTION 'Workspace % must have at least one owner', target
            USING ERRCODE = 'check_violation', CONSTRAINT = 'workspace_has_owner';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=4000), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_workspaces_created_by_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')", name=op.f("ck_workspace_members_role")
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["users.id"],
            name=op.f("fk_workspace_members_added_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_members_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_members_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", name=op.f("pk_workspace_members")),
    )
    op.create_index(
        op.f("ix_workspace_members_user_id"), "workspace_members", ["user_id"], unique=False
    )
    op.execute(_OWNER_CHECK)
    # Deferred to commit, so a Workspace and its first owner can be inserted in either
    # order, and an ownership transfer can add the new owner after removing the old.
    # Not a lock: two concurrent transactions each demoting or removing a different
    # one of two owners both see the other owner still there and both commit. Every
    # member-management path must lock the Workspace row (SELECT ... FOR UPDATE) first.
    op.execute(
        "CREATE CONSTRAINT TRIGGER workspace_has_owner AFTER INSERT ON workspaces "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION workspace_must_have_owner()"
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER workspace_keeps_an_owner "
        "AFTER UPDATE OR DELETE ON workspace_members "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION workspace_must_have_owner()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER workspace_keeps_an_owner ON workspace_members")
    op.execute("DROP TRIGGER workspace_has_owner ON workspaces")
    op.execute("DROP FUNCTION workspace_must_have_owner()")
    op.drop_index(op.f("ix_workspace_members_user_id"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
