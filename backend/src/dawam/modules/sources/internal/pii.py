"""PII findings from column names, and the one Protected Column policy (spec §6.5, §6.12).

``scan_columns`` runs inside the Snapshot writer's transaction for every new Snapshot:
it applies the name rules to the Snapshot's columns and records a ``suggested`` finding
for each column and rule that has none yet. It reads and writes only app-database rows,
so it puts no load on the source.

``is_protected`` is the only definition of a Protected Column: flagged ``is_sensitive``
**or** holding a finding that is ``suggested`` or ``confirmed``. Dismissing a finding
removes protection only when the column is not flagged. Profiling, AI masking, lineage and
exports all ask here, never re-derive it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..tables import (
    PiiCustomRuleRecord,
    PiiDisabledRuleRecord,
    PiiFindingRecord,
    SrcColumnRecord,
)
from .pii_rules import (
    DEFAULT_RULES,
    NAME_CONFIDENCE_FLOOR,
    RuleSet,
    compile_custom_rule,
    match_name,
)

PROTECTING_STATUSES = ("suggested", "confirmed")


def is_protected(*, is_sensitive: bool, finding_statuses: Iterable[str]) -> bool:
    """Whether a column is protected: ``is_sensitive`` or a ``suggested``/``confirmed``
    finding."""
    return is_sensitive or any(s in PROTECTING_STATUSES for s in finding_statuses)


def protected_column_ids(db: Session, column_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The Protected Columns among ``column_ids``, by the one policy of ``is_protected``."""
    wanted = list(column_ids)
    if not wanted:
        return set()
    sensitive = sa.select(SrcColumnRecord.id).where(
        SrcColumnRecord.id.in_(wanted), SrcColumnRecord.is_sensitive
    )
    found = sa.select(PiiFindingRecord.src_column_id).where(
        PiiFindingRecord.src_column_id.in_(wanted),
        PiiFindingRecord.status.in_(PROTECTING_STATUSES),
    )
    return set(db.scalars(sensitive)) | set(db.scalars(found))


def load_rule_set(db: Session, workspace_id: uuid.UUID) -> RuleSet:
    """The Workspace's PII rules in force: its custom rules and its disabled built-ins."""
    disabled = frozenset(
        db.scalars(
            sa.select(PiiDisabledRuleRecord.rule).where(
                PiiDisabledRuleRecord.workspace_id == workspace_id
            )
        )
    )
    custom = []
    for record in db.scalars(
        sa.select(PiiCustomRuleRecord)
        .where(PiiCustomRuleRecord.workspace_id == workspace_id)
        .order_by(PiiCustomRuleRecord.name)
    ):
        try:
            custom.append(
                compile_custom_rule(
                    record.name,
                    keywords=list(record.keywords),
                    pattern=record.pattern,
                    category=record.category,
                    confidence=record.confidence,
                )
            )
        except ValueError:  # validated on the way in; a stale row must not stop a scan
            continue
    return RuleSet(disabled=disabled, custom=tuple(custom))


def scan_columns(
    db: Session,
    *,
    snapshot_id: uuid.UUID,
    columns: Iterable[tuple[uuid.UUID, str]],
    at: datetime,
    rules: RuleSet = DEFAULT_RULES,
) -> int:
    """Name-scan ``columns`` (id and name pairs) of a new Snapshot; return how many new
    findings were recorded. A column that already has a finding for a rule keeps it,
    whatever its status, so a decision is never undone by a later Snapshot. ``rules`` are
    the Workspace's (``load_rule_set``)."""
    matches = [(cid, m) for cid, name in columns if (m := match_name(name, rules)) is not None]
    if not matches:
        return 0
    known: set[tuple[uuid.UUID, str]] = set()
    ids = [cid for cid, _ in matches]
    for start in range(0, len(ids), 5000):
        known.update(
            tuple(row)
            for row in db.execute(
                sa.select(PiiFindingRecord.src_column_id, PiiFindingRecord.rule).where(
                    PiiFindingRecord.src_column_id.in_(ids[start : start + 5000])
                )
            )
        )
    rows = [
        {
            "id": uuid.uuid4(),
            "src_column_id": cid,
            "rule": m.rule,
            "category": m.category,
            "confidence": max(m.confidence, NAME_CONFIDENCE_FLOOR),
            "evidence": m.evidence[:500],
            "status": "suggested",
            "snapshot_id": snapshot_id,
            "detected_at": at,
        }
        for cid, m in matches
        if (cid, m.rule) not in known
    ]
    for start in range(0, len(rows), 5000):
        db.execute(sa.insert(PiiFindingRecord), rows[start : start + 5000])
    return len(rows)
