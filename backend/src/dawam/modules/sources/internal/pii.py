"""PII findings from column names, and the one Protected Column policy (spec §6.5, §6.12).

``scan_columns`` runs inside the Snapshot writer's transaction for every new Snapshot:
it applies the name rules to the Snapshot's columns and records a ``suggested`` finding
for each column and rule that has none yet. It reads and writes only app-database rows,
so it puts no load on the source.

``reopen_rejected`` is the one rule that sends a rejected column back to review: a scan
found a rule that this scan's source never matched it with, or raised a rule's confidence
above the confidence it had when rejected (spec §6.12). Every source (name, value, a
query's values, AI) asks here, and each writes and updates only its own rows: the same rule
can hold a ``name``, a ``value`` and an ``ai`` finding on one column.

``is_protected`` is the only definition of a Protected Column: flagged ``is_sensitive``
**or** holding a finding that is ``suggested`` or ``accepted``. Rejecting a finding
removes protection only when the column is not flagged. Profiling, AI masking, lineage and
exports all ask here, never re-derive it.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Iterator, Sequence
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

logger = logging.getLogger(__name__)

PROTECTING_STATUSES = ("suggested", "accepted")


def is_protected(*, is_sensitive: bool, finding_statuses: Iterable[str]) -> bool:
    """Whether a column is protected: ``is_sensitive`` or a ``suggested``/``accepted``
    finding."""
    return is_sensitive or any(s in PROTECTING_STATUSES for s in finding_statuses)


def protected_column_ids(
    db: Session, column_ids: Iterable[uuid.UUID] | sa.Select[tuple[uuid.UUID]]
) -> set[uuid.UUID]:
    """The Protected Columns among ``column_ids``, by the one policy of ``is_protected``.
    ``column_ids`` may be a SELECT of the ids, so a whole Snapshot's columns are matched by
    a sub-select instead of one bind parameter each."""
    wanted: list[uuid.UUID] | sa.Select[tuple[uuid.UUID]]
    if isinstance(column_ids, sa.Select):
        wanted = column_ids
    else:
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
            logger.warning(
                "The stored custom PII rule %s no longer compiles; skipped.", record.name
            )
    return RuleSet(disabled=disabled, custom=tuple(custom))


TEXT_PATTERN_TIMEOUT_SECONDS = 0.05


def custom_rules_in_text(db: Session, workspace_id: uuid.UUID, text: str) -> list[str]:
    """The ids of the Workspace's custom PII rules whose regex matches anywhere in ``text``.
    Keywords match column names, not prose, so they are not tried. A regex that runs past the
    time limit counts as a match: the text is refused rather than let through."""
    hits: list[str] = []
    for custom in load_rule_set(db, workspace_id).custom:
        if custom.pattern is None:
            continue
        try:
            if custom.pattern.search(text, timeout=TEXT_PATTERN_TIMEOUT_SECONDS):
                hits.append(custom.id)
        except TimeoutError:
            hits.append(custom.id)
    return hits


def reopens(finding: PiiFindingRecord | None, confidence: float) -> bool:
    """Whether a scan that scored a rule at ``confidence`` sends the column back to review:
    the rule never matched it (``finding`` is ``None``), or the rejected finding's
    confidence rises above the one it had when rejected."""
    if finding is None:
        return True
    return (
        finding.status == "rejected"
        and finding.confidence_at_rejection is not None
        and confidence > finding.confidence_at_rejection
    )


# The most ids one ``IN (...)`` carries, so a very wide selection stays under the database's
# parameter limit.
ID_CHUNK = 5000


def id_chunks(ids: Sequence[uuid.UUID]) -> Iterator[Sequence[uuid.UUID]]:
    """``ids`` in slices of ``ID_CHUNK``."""
    for start in range(0, len(ids), ID_CHUNK):
        yield ids[start : start + ID_CHUNK]


def rejected_column_ids(db: Session, column_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The columns among ``column_ids`` that have a rejected finding."""
    ids = list(set(column_ids))
    return {
        cid
        for chunk in id_chunks(ids)
        for cid in db.scalars(
            sa.select(PiiFindingRecord.src_column_id)
            .where(PiiFindingRecord.src_column_id.in_(chunk), PiiFindingRecord.status == "rejected")
            .distinct()
        )
    }


def reopen_rejected(db: Session, column_ids: Iterable[uuid.UUID]) -> None:
    """Send the rejected findings of ``column_ids`` back to the review queue, together: a
    column is reviewed as a whole. Who rejected them and when stay, so the queue can say
    the column was rejected before. A column that is accepted PII now is left alone."""
    ids = list(set(column_ids))
    if not ids:
        return
    accepted = set(
        db.scalars(
            sa.select(SrcColumnRecord.id).where(
                SrcColumnRecord.id.in_(ids), SrcColumnRecord.is_sensitive
            )
        )
    ) | set(
        db.scalars(
            sa.select(PiiFindingRecord.src_column_id).where(
                PiiFindingRecord.src_column_id.in_(ids), PiiFindingRecord.status == "accepted"
            )
        )
    )
    wanted = [i for i in ids if i not in accepted]
    for chunk in id_chunks(wanted):
        db.execute(
            sa.update(PiiFindingRecord)
            .where(
                PiiFindingRecord.src_column_id.in_(chunk),
                PiiFindingRecord.status == "rejected",
            )
            .values(status="suggested", removed=False)
            .execution_options(synchronize_session=False)
        )


def upsert_finding(
    db: Session,
    column_id: uuid.UUID,
    *,
    source: str,
    rule: str,
    category: str,
    confidence: float,
    evidence: str,
    at: datetime,
    rejected_columns: set[uuid.UUID] | None = None,
) -> bool:
    """Record what a value scan or the AI detection (``source``) found for ``rule`` on a
    column, writing only that source's row: a new row is ``suggested``; an existing one keeps
    its status unless ``reopens`` sends the column back to review, its confidence rises to
    the higher one and its evidence changes only while it waits for review. Returns whether
    the row is new. A caller writing many findings passes ``rejected_columns`` (from
    ``rejected_column_ids``) so the rejected state is not read again per finding; a reopen
    removes the column from it."""
    existing = db.scalars(
        sa.select(PiiFindingRecord)
        .where(
            PiiFindingRecord.src_column_id == column_id,
            PiiFindingRecord.rule == rule,
            PiiFindingRecord.source == source,
        )
        .with_for_update()
    ).first()
    if rejected_columns is None:
        rejected_columns = rejected_column_ids(db, [column_id])
    if column_id in rejected_columns and reopens(existing, confidence):
        reopen_rejected(db, [column_id])
        rejected_columns.discard(column_id)
        if existing is not None:
            db.refresh(existing)
    if existing is None:
        db.add(
            PiiFindingRecord(
                id=uuid.uuid4(),
                src_column_id=column_id,
                rule=rule,
                category=category,
                confidence=confidence,
                evidence=evidence[:500],
                status="suggested",
                source=source,
                snapshot_id=None,
                detected_at=at,
            )
        )
        return True
    # A decision stays unless the scan reopened it: the confidence moves, and the evidence
    # only on a finding waiting for review.
    existing.confidence = max(existing.confidence, confidence)
    if existing.status == "suggested":
        existing.evidence = evidence[:500]
    return False


def scan_columns(
    db: Session,
    *,
    snapshot_id: uuid.UUID,
    columns: Iterable[tuple[uuid.UUID, str]],
    at: datetime,
    rules: RuleSet = DEFAULT_RULES,
) -> int:
    """Name-scan ``columns`` (id and name pairs) of a new Snapshot; return how many new
    findings were recorded. Only ``name`` rows are written or updated. A column that already
    has a name finding for a rule keeps its status, so a decision survives a later Snapshot,
    unless the scan finds a new rule on a rejected column or raises a rejected rule's
    confidence (``reopens``). ``rules`` are the Workspace's (``load_rule_set``)."""
    matches = [(cid, m) for cid, name in columns if (m := match_name(name, rules)) is not None]
    if not matches:
        return 0
    known: dict[tuple[uuid.UUID, str], PiiFindingRecord] = {}
    rejected: set[uuid.UUID] = set()
    accepted: set[uuid.UUID] = set()
    ids = [cid for cid, _ in matches]
    for start in range(0, len(ids), 5000):
        for finding in db.scalars(
            sa.select(PiiFindingRecord).where(
                PiiFindingRecord.src_column_id.in_(ids[start : start + 5000])
            )
        ):
            if finding.source == "name":  # the name scan writes only its own rows
                known[(finding.src_column_id, finding.rule)] = finding
            if finding.status == "rejected":
                rejected.add(finding.src_column_id)
            elif finding.status == "accepted":
                accepted.add(finding.src_column_id)
        accepted.update(
            db.scalars(
                sa.select(SrcColumnRecord.id).where(
                    SrcColumnRecord.id.in_(ids[start : start + 5000]), SrcColumnRecord.is_sensitive
                )
            )
        )
    # A column accepted as PII now is never reopened (``reopen_rejected``).
    rejected -= accepted
    reopen: set[uuid.UUID] = set()
    for cid, m in matches:
        confidence = max(m.confidence, NAME_CONFIDENCE_FLOOR)
        existing = known.get((cid, m.rule))
        if cid in rejected and reopens(existing, confidence):
            reopen.add(cid)
        if existing is not None and confidence > existing.confidence:
            existing.confidence = confidence
            if existing.status == "suggested" or cid in reopen:
                existing.evidence = m.evidence[:500]
    db.flush()
    reopen_rejected(db, reopen)
    rows = [
        {
            "id": uuid.uuid4(),
            "src_column_id": cid,
            "rule": m.rule,
            "category": m.category,
            "confidence": max(m.confidence, NAME_CONFIDENCE_FLOOR),
            "evidence": m.evidence[:500],
            "status": "suggested",
            "source": "name",
            "snapshot_id": snapshot_id,
            "detected_at": at,
        }
        for cid, m in matches
        if (cid, m.rule) not in known
    ]
    for start in range(0, len(rows), 5000):
        db.execute(sa.insert(PiiFindingRecord), rows[start : start + 5000])
    return len(rows)
