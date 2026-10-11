"""PII propagation over the stored lineage edges (spec §6.12, story 137).

A DW column is PII-derived only through ``value`` edges from a confirmed PII source column.
A table is PII-influenced when a PII source column or a PII-derived column only steers it:
a ``uses`` edge (join, filter, GROUP BY, a lookup's natural-key input) or a ``lookup``
edge. A lookup's surrogate key has no ``value`` edge, so it is never PII-derived. The
result is computed from the edges as they are, so it is current after every mapping change.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import LineageEdgeRecord


@dataclass(frozen=True)
class PiiPropagation:
    derived_columns: frozenset[uuid.UUID]
    """DW columns fed by a confirmed PII column through value edges."""
    influenced_tables: frozenset[uuid.UUID]
    """DW tables steered by PII but not necessarily holding PII-derived columns."""


def propagate_pii(db: Session, pii_column_ids: Iterable[uuid.UUID]) -> PiiPropagation:
    """Follow value edges down from the confirmed PII source columns, then collect the
    tables that PII (source or derived) steers."""
    seeds = set(pii_column_ids)
    derived: set[uuid.UUID] = set()
    frontier = set(seeds)
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
        frontier = reached - derived
        derived |= reached
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
    return PiiPropagation(frozenset(derived), frozenset(influenced))
