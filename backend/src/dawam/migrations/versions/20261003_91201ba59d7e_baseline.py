"""baseline: the empty starting point; modules add their tables in later revisions.

Revision ID: 91201ba59d7e
Revises: (none)
Create Date: 2026-10-03 23:51:48.017525
"""

from collections.abc import Sequence

revision: str = "91201ba59d7e"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
