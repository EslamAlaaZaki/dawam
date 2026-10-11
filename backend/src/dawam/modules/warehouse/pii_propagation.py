"""PII propagation over the stored lineage edges (spec §6.12, story 137).

A DW column is PII-derived only through ``value`` edges from a confirmed PII source column.
A table is PII-influenced when a PII source column or a PII-derived column only steers it:
a ``uses`` edge (join, filter, GROUP BY, a lookup's natural-key input) or a ``lookup``
edge. A lookup's surrogate key has no ``value`` edge, so it is never PII-derived. A
PII-derived column whose mapping is ``hashed``, and every column it feeds, stays PII-derived
and is also pseudonymised (story 138). The result is computed from the edges and mappings as
they are, so it is current after every mapping change.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import ColumnMappingRecord, DwColumnRecord, LineageEdgeRecord


@dataclass(frozen=True)
class PiiPropagation:
    derived_columns: frozenset[uuid.UUID]
    """DW columns fed by a confirmed PII column through value edges."""
    influenced_tables: frozenset[uuid.UUID]
    """DW tables steered by PII but not necessarily holding PII-derived columns."""
    pseudonymised_columns: frozenset[uuid.UUID] = frozenset()
    """The PII-derived columns that are hashed or fed by a hashed one."""


def propagate_pii(db: Session, pii_column_ids: Iterable[uuid.UUID]) -> PiiPropagation:
    """Follow value edges down from the confirmed PII source columns, then collect the
    tables that PII (source or derived) steers."""
    seeds = set(pii_column_ids)
    derived = _downstream(db, seeds)
    hashed = (
        set(
            db.scalars(
                sa.select(ColumnMappingRecord.dw_column_id).where(
                    ColumnMappingRecord.mapping_type == "hashed",
                    ColumnMappingRecord.dw_column_id.in_(derived),
                )
            )
        )
        if derived
        else set()
    )
    pseudonymised = (hashed | _downstream(db, hashed)) & derived
    carriers = seeds | derived
    influenced = (
        set(
            db.scalars(
                sa.select(LineageEdgeRecord.to_id).where(
                    LineageEdgeRecord.kind.in_(("uses", "lookup")),
                    LineageEdgeRecord.to_type == "dw_table",
                    LineageEdgeRecord.from_id.in_(carriers),
                )
            )
        )
        if carriers
        else set()
    )
    return PiiPropagation(frozenset(derived), frozenset(influenced), frozenset(pseudonymised))


def pii_origins(
    db: Session, pii_column_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[uuid.UUID]]:
    """For each DW column reached by value edges, the given PII source columns it is fed
    by (the DW columns ``propagate_pii`` calls derived, with where their PII comes from)."""
    origins: dict[uuid.UUID, set[uuid.UUID]] = {}
    for seed in set(pii_column_ids):
        for reached in _downstream(db, {seed}):
            origins.setdefault(reached, set()).add(seed)
    return origins


def _downstream(db: Session, start: set[uuid.UUID]) -> set[uuid.UUID]:
    """The DW columns reached from ``start`` through ``value`` edges."""
    reached_all: set[uuid.UUID] = set()
    frontier = set(start)
    while frontier:
        reached = set(
            db.scalars(
                sa.select(LineageEdgeRecord.to_id).where(
                    LineageEdgeRecord.kind == "value",
                    LineageEdgeRecord.to_type == "dw_column",
                    LineageEdgeRecord.from_id.in_(frontier),
                )
            )
        )
        frontier = reached - reached_all
        reached_all |= reached
    return reached_all


def pii_notes(db: Session, column_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The handling note of each given DW column that has one."""
    ids = list(column_ids)
    if not ids:
        return {}
    return dict(
        db.execute(  # type: ignore[arg-type]
            sa.select(DwColumnRecord.id, DwColumnRecord.pii_note).where(
                DwColumnRecord.id.in_(ids), DwColumnRecord.pii_note.is_not(None)
            )
        ).all()
    )
