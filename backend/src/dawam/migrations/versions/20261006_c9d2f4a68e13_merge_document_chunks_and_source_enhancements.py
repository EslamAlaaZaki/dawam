"""merge the document chunks and source enhancements heads.

Revision ID: c9d2f4a68e13
Revises: b2c8e5d1a047, b3c8d5e1f704
Create Date: 2026-10-06 16:00:00.000000
"""

from collections.abc import Sequence

revision: str = "c9d2f4a68e13"
down_revision: str | Sequence[str] | None = ("b2c8e5d1a047", "b3c8d5e1f704")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
