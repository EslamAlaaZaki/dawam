"""Stem the full-text column: Arabic and English word forms meet their stems.

Revision ID: e4b8a1c6d937
Revises: d3b9f4a6e251
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from dawam.modules.files.internal.arabic import stemmed

revision: str = "e4b8a1c6d937"
down_revision: str | Sequence[str] | None = "d3b9f4a6e251"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 500


def upgrade() -> None:
    """Re-stem every passage; ``search_vector`` is generated from ``search_text``, so it
    follows."""
    conn = op.get_bind()
    last = None
    while True:
        rows = conn.execute(
            sa.text(
                "SELECT id, search_text FROM document_chunks "
                "WHERE (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid)) "
                "ORDER BY id LIMIT :n"
            ),
            {"last": last, "n": _BATCH},
        ).all()
        if not rows:
            return
        conn.execute(
            sa.text("UPDATE document_chunks SET search_text = :t WHERE id = :id"),
            [{"id": r.id, "t": stemmed(r.search_text)} for r in rows],
        )
        last = str(rows[-1].id)


def downgrade() -> None:
    """Stemming is lossy and the passage text is intact: a re-index restores the old column."""
