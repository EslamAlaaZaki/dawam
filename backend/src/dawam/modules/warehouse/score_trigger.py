"""Recalculating the score after every design change, debounced (spec §6.11, story 118).

A design change is any committed write to the DW Schema or its mappings, whoever made it
(the model editor, a mapping edit, a Change Set, propagation): the module watches what a
session flushes rather than relying on each writer to remember. Once the transaction
commits, the Data Warehouse is handed to a ``ScoreScheduler``, which scores it after a quiet
moment (``debounce_seconds``; 0 scores at once, on the committing thread, for tests). A burst
of changes is therefore scored once, from the database as it stands when the timer fires.
"""

from __future__ import annotations

import logging
import threading
import uuid
import weakref
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .tables import (
    ColumnMappingRecord,
    DataWarehouseRecord,
    DwColumnRecord,
    DwTableRecord,
    MappingBranchRecord,
    TableMappingRecord,
)

logger = logging.getLogger("dawam.score")

_HINTS = "dawam.score.touched"
"""``Session.info`` key: what the session's flushes touched, resolved on commit."""
Hint = tuple[str, uuid.UUID]


class ScoreScheduler:
    """Scores a Data Warehouse ``debounce_seconds`` after the first change of a burst."""

    def __init__(
        self, recalculate: Callable[[uuid.UUID], object], *, debounce_seconds: float
    ) -> None:
        self._recalculate = recalculate
        self._debounce = debounce_seconds
        self._lock = threading.Lock()
        self._timers: dict[uuid.UUID, threading.Timer] = {}

    def request(self, data_warehouse_id: uuid.UUID) -> None:
        if self._debounce <= 0:
            self._run(data_warehouse_id)
            return
        with self._lock:
            if data_warehouse_id in self._timers:
                return  # the pending run will read everything changed until it fires
            timer = threading.Timer(self._debounce, self._fire, [data_warehouse_id])
            timer.daemon = True
            self._timers[data_warehouse_id] = timer
            timer.start()

    def shutdown(self) -> None:
        """Drop the pending runs (the application is stopping)."""
        with self._lock:
            timers, self._timers = list(self._timers.values()), {}
        for timer in timers:
            timer.cancel()

    def _fire(self, data_warehouse_id: uuid.UUID) -> None:
        with self._lock:
            self._timers.pop(data_warehouse_id, None)
        self._run(data_warehouse_id)

    def _run(self, data_warehouse_id: uuid.UUID) -> None:
        try:
            self._recalculate(data_warehouse_id)
        except Exception:
            # A failed score must never fail the change that asked for it.
            logger.exception(
                "score recalculation failed", extra={"data_warehouse": str(data_warehouse_id)}
            )


_SCHEDULERS: weakref.WeakKeyDictionary[sa.Engine, ScoreScheduler] = weakref.WeakKeyDictionary()


def install(engine: sa.Engine, scheduler: ScoreScheduler) -> None:
    """Score the Data Warehouses that sessions of ``engine`` change, through ``scheduler``."""
    _SCHEDULERS[engine] = scheduler


def uninstall(engine: sa.Engine) -> None:
    scheduler = _SCHEDULERS.pop(engine, None)
    if scheduler is not None:
        scheduler.shutdown()


def _hint_of(obj: Any) -> Hint | None:
    if isinstance(obj, DataWarehouseRecord):
        return "warehouse", obj.id
    if isinstance(obj, DwTableRecord):
        return "warehouse", obj.data_warehouse_id
    if isinstance(obj, DwColumnRecord):
        return "table", obj.table_id
    if isinstance(obj, TableMappingRecord):
        return "table", obj.dw_table_id
    if isinstance(obj, (MappingBranchRecord, ColumnMappingRecord)):
        return "mapping", obj.table_mapping_id
    return None


def touch(session: Session, kind: str, id: uuid.UUID) -> None:
    """Note that the session changed design that belongs to a warehouse (``kind``
    ``warehouse``), a table or a table mapping: for a writer that cannot be seen to flush
    (a bulk statement)."""
    session.info.setdefault(_HINTS, set()).add((kind, id))


def _on_flush(session: Session, _context: object) -> None:
    if session.bind not in _SCHEDULERS:
        return
    for obj in (*session.new, *session.dirty, *session.deleted):
        hint = _hint_of(obj)
        if hint is not None:
            session.info.setdefault(_HINTS, set()).add(hint)


def _warehouses_of(engine: sa.Engine, hints: set[Hint]) -> set[uuid.UUID]:
    found = {id for kind, id in hints if kind == "warehouse"}
    tables = [id for kind, id in hints if kind == "table"]
    mappings = [id for kind, id in hints if kind == "mapping"]
    with engine.connect() as conn:
        if tables:
            found.update(
                conn.scalars(
                    sa.select(DwTableRecord.data_warehouse_id).where(DwTableRecord.id.in_(tables))
                )
            )
        if mappings:
            found.update(
                conn.scalars(
                    sa.select(DwTableRecord.data_warehouse_id)
                    .join(TableMappingRecord, TableMappingRecord.dw_table_id == DwTableRecord.id)
                    .where(TableMappingRecord.id.in_(mappings))
                )
            )
    return found


def _on_commit(session: Session) -> None:
    hints = session.info.pop(_HINTS, None)
    scheduler = _SCHEDULERS.get(session.bind) if hints else None
    if not hints or scheduler is None:
        return
    engine = session.bind
    assert isinstance(engine, sa.Engine)
    try:
        for warehouse_id in _warehouses_of(engine, hints):
            scheduler.request(warehouse_id)
    except Exception:
        logger.exception("could not schedule a score recalculation")


def _on_rollback(session: Session) -> None:
    session.info.pop(_HINTS, None)


sa.event.listen(Session, "after_flush", _on_flush)
sa.event.listen(Session, "after_commit", _on_commit)
sa.event.listen(Session, "after_soft_rollback", lambda session, _previous: _on_rollback(session))
