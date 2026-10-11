"""Regeneration offers (spec §6.9 "Regeneration", "Manual mapping edits"; stories 98, 98a).

DAWAM offers, and never runs, a regeneration of the DW Schema when a mandatory input of the
generator changes: a new Snapshot (``RegenerationOffers.after_snapshot``, which the
composition root calls after the staging sync), a KPI change, or a Source Schema enhancement
the generator reads (a table's classification or SCD hint, a relationship accepted or
rejected). A manual mapping edit that adds an input to a Core or Mart table (a column, or a
table, of the Layer below that fed none of its columns before) offers a regeneration of only
that table. The offer is a ``regeneration`` notification to the owners and editors; whoever
takes it asks the assistant to regenerate, which ends in a Change Set as always.

Like the score trigger and the Layer Approval, this module does not rely on each writer: it
reads what a transaction audited (``audited_changes``) just before it commits and writes the
offer in that transaction. A change whose origin is regeneration or propagation (its audit
channel) never offers anything, and the mapping writes of an accepted Change Set never note
new inputs (only ``MappingService``'s direct edits do), so there is no loop. Offers are made
only once the Data Warehouse has been generated (a Core or Mart table carries a
``generation_key``), and a member who has not read an offer gets no second one of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import AuditedChange, audited_changes
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import WorkspaceService
from dawam.platform.clock import Clock

from .approvals import installed_clock
from .tables import DataWarehouseRecord, DwColumnRecord, DwTableRecord, LineageEdgeRecord

MODEL_LAYERS = ("core", "mart")
"""The Layers the generator writes (``model_service`` imports this module's caller)."""
KIND = "regeneration"
"""The notification kind of an offer."""
NO_OFFER_VIA = frozenset({"regeneration", "propagation"})
"""Audit channels whose changes never offer a regeneration (no loop)."""
KPI_INPUT_FIELDS = frozenset(
    {"source_system_id", "name", "definition", "formula_text", "unit", "aggregation"}
)
"""What the generator reads of a KPI. Its links and formula SQL follow the DW Schema, and its
status, owner, targets and refresh frequency do not shape it."""
SOURCE_TABLE_INPUT_FIELDS = frozenset({"classification", "scd_hint"})
REASONS = {
    "snapshot": "a new Snapshot",
    "kpi": "KPI changes",
    "source_schema": "Source Schema enhancements",
}
_TABLES = "dawam.regeneration.tables"
"""``Session.info`` key: the Core and Mart tables a direct mapping edit gave new inputs."""


def mandatory_input(change: AuditedChange) -> str | None:
    """Which mandatory input of the generator an audited change changed (a ``REASONS`` key),
    or ``None`` when it changed none, or came from regeneration or propagation."""
    if change.via in NO_OFFER_VIA:
        return None
    if change.old is None or change.new is None:
        changed: set[str] | None = None  # created or deleted
    else:
        changed = {f for f in {*change.old, *change.new} if change.old.get(f) != change.new.get(f)}
    if change.entity_type == "kpi":
        return "kpi" if changed is None or changed & KPI_INPUT_FIELDS else None
    if change.entity_type == "source_table":
        return "source_schema" if changed and changed & SOURCE_TABLE_INPUT_FIELDS else None
    if change.entity_type == "relationship":
        return "source_schema" if changed is None or "status" in changed else None
    return None


def table_inputs(db: Session, table_id: uuid.UUID) -> set[uuid.UUID]:
    """The columns of the Layer below that a table's mapping reads (its columns' ``value``
    and ``uses`` edges and its branches' ``uses`` edges; a lookup's dimension is a declared
    reference, not an input), and their tables."""
    columns = sa.select(DwColumnRecord.id).where(DwColumnRecord.table_id == table_id)
    read = (
        sa.select(DwColumnRecord.id, DwColumnRecord.table_id)
        .join(LineageEdgeRecord, LineageEdgeRecord.from_id == DwColumnRecord.id)
        .where(
            LineageEdgeRecord.from_type == "dw_column",
            LineageEdgeRecord.kind.in_(("value", "uses")),
            sa.or_(
                sa.and_(
                    LineageEdgeRecord.to_type == "dw_column",
                    LineageEdgeRecord.to_id.in_(columns),
                ),
                sa.and_(
                    LineageEdgeRecord.to_type == "dw_table", LineageEdgeRecord.to_id == table_id
                ),
            ),
        )
    )
    found: set[uuid.UUID] = set()
    for column_id, input_table_id in db.execute(read):
        found.update((column_id, input_table_id))
    return found


def note_new_inputs(db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID) -> None:
    """A direct mapping edit in ``db``'s transaction gave the table an input it did not
    read before: offer a regeneration of that table when the transaction commits."""
    db.info.setdefault(_TABLES, set()).add((workspace_id, table_id))


def _generated_warehouse(db: Session, workspace_id: uuid.UUID) -> DataWarehouseRecord | None:
    """The Workspace's Data Warehouse, once a Core or Mart table of it was generated."""
    return db.scalars(
        sa.select(DataWarehouseRecord).where(
            DataWarehouseRecord.workspace_id == workspace_id,
            sa.exists().where(
                DwTableRecord.data_warehouse_id == DataWarehouseRecord.id,
                DwTableRecord.layer.in_(MODEL_LAYERS),
                DwTableRecord.generation_key.is_not(None),
            ),
        )
    ).first()


def _offer_message(reasons: Iterable[str]) -> str:
    named = [REASONS[r] for r in dict.fromkeys(reasons)]
    what = named[0] if len(named) == 1 else ", ".join(named[:-1]) + " and " + named[-1]
    return (
        f"A mandatory input of the DW Schema changed ({what}). Ask the assistant to "
        "regenerate the Core and Mart Layers: you review the result as a Change Set, and "
        "nothing you overrode changes unless you accept it."
    )


def _table_message(table: DwTableRecord) -> str:
    return (
        f"Your mapping edit changed the inputs of {table.layer}.{table.name}. Ask the "
        f"assistant to regenerate only {table.name}: you review the result as a Change Set."
    )


class RegenerationOffers:
    """Offers regenerations to a Workspace's owners and editors."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._notifications = NotificationService(engine, clock=clock)

    def after_snapshot(self, workspace_id: uuid.UUID) -> int:
        """A new Snapshot was taken: offer to regenerate. How many members were offered it."""
        with Session(self._engine) as db, db.begin():
            return self.offer(db, workspace_id, ["snapshot"])

    def offer(self, db: Session, workspace_id: uuid.UUID, reasons: Sequence[str]) -> int:
        """Offer, in ``db``'s transaction, to regenerate the DW Schema because of
        ``reasons`` (``REASONS`` keys). Nothing before the first generation."""
        warehouse = _generated_warehouse(db, workspace_id)
        if warehouse is None:
            return 0
        return self._notifications.notify(
            self._workspaces.reviewer_ids(workspace_id),
            kind=KIND,
            message=_offer_message(reasons),
            workspace_id=workspace_id,
            ref_type="data_warehouse",
            ref_id=warehouse.id,
            db=db,
            unless_unread=True,
        )

    def offer_table(self, db: Session, workspace_id: uuid.UUID, table_id: uuid.UUID) -> int:
        """Offer, in ``db``'s transaction, to regenerate only one Core or Mart table."""
        warehouse = _generated_warehouse(db, workspace_id)
        table = db.get(DwTableRecord, table_id)
        if (
            warehouse is None
            or table is None
            or table.data_warehouse_id != warehouse.id
            or table.layer not in MODEL_LAYERS
        ):
            return 0
        return self._notifications.notify(
            self._workspaces.reviewer_ids(workspace_id),
            kind=KIND,
            message=_table_message(table),
            workspace_id=workspace_id,
            ref_type="dw_table",
            ref_id=table.id,
            db=db,
            unless_unread=True,
        )


def offer_reasons(changes: Iterable[AuditedChange]) -> dict[uuid.UUID, list[str]]:
    """The ``REASONS`` keys each Workspace's audited ``changes`` offer a regeneration for."""
    found: dict[uuid.UUID, list[str]] = {}
    for change in changes:
        reason = mandatory_input(change)
        if reason is not None:
            found.setdefault(change.workspace_id, []).append(reason)
    return found


def _before_commit(session: Session) -> None:
    tables: set[tuple[uuid.UUID, uuid.UUID]] = session.info.pop(_TABLES, set())
    reasons = offer_reasons(audited_changes(session))
    if not reasons and not tables:
        return
    bind = session.get_bind()
    engine = bind if isinstance(bind, sa.Engine) else bind.engine
    clock = installed_clock(engine)
    offers = RegenerationOffers(
        engine, workspaces=WorkspaceService(engine, clock=clock), clock=clock
    )
    with session.no_autoflush:
        for workspace_id, found in reasons.items():
            offers.offer(session, workspace_id, found)
        for workspace_id, table_id in sorted(tables):
            offers.offer_table(session, workspace_id, table_id)


def _forget(session: Session) -> None:
    session.info.pop(_TABLES, None)


sa.event.listen(Session, "before_commit", _before_commit)
sa.event.listen(Session, "after_soft_rollback", lambda session, _previous: _forget(session))
