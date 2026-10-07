"""The Change Set engine (spec §6.10 "Change Set rules", stories 147, 148).

``ChangeSetService`` has four verbs: ``propose`` (a new Change Set, superseding a pending
one of the same origin and scope), ``get`` / ``list`` (the diff), ``accept`` and
``reject`` (all items or some, with dependency closure). The rules live here and nowhere
else; what each object type looks like lives in its module's ``ObjectHandler``.

``accept`` is one transaction on the locked Change Set row: a second accept of a closed
Change Set is 409, stale items (a field they change moved since they were proposed) and
their dependents are skipped and reported, every other item is applied and audited, and a
failing item aborts all of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import Action, WorkspaceService, required_role
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .handlers import ObjectHandlers
from .tables import (
    CHANGE_SET_STATUSES,
    LABEL_MAX_LENGTH,
    OPERATIONS,
    ORIGINS,
    REASON_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    ChangeSetItemRecord,
    ChangeSetRecord,
)

MAX_ITEMS = 5000
"""Items in one Change Set (a first sync of 2,000 tables is about 2,000 items)."""

OPEN_STATUSES = ("pending", "needs_owner")
"""Item statuses that still await a decision."""

ItemStatus = Literal["pending", "needs_owner", "accepted", "rejected", "stale", "expired"]


@dataclass(frozen=True)
class ProposedItem:
    """One item of a Change Set being proposed."""

    key: str
    """Names the item inside this proposal, for ``depends_on``."""
    object_type: str
    operation: str
    """``create`` (one item per table, columns nested in ``payload``), ``update`` or
    ``delete`` (one item per object)."""
    payload: Mapping[str, Any]
    """A create's values; an update's new values of the changed fields; whatever the
    handler needs for a delete."""
    object_id: uuid.UUID | None = None
    label: str = ""
    """What the diff shows, e.g. ``core.customers``."""
    base_values: Mapping[str, Any] | None = None
    """The current values of the fields the item changes. Left out, an update records the
    current values of the fields in ``payload``; give it for a delete that guards fields."""
    depends_on: Sequence[str] = ()
    """Keys of earlier items this one needs (e.g. an FK needs the table it points at)."""
    is_conflict: bool = False
    """The item changes a field the user overrode: shown as a conflict."""


@dataclass(frozen=True)
class ChangeSetItem:
    id: uuid.UUID
    position: int
    object_type: str
    object_id: uuid.UUID | None
    operation: str
    label: str
    base_values: dict[str, Any] | None
    payload: dict[str, Any]
    depends_on: list[uuid.UUID]
    required_role: str
    is_conflict: bool
    status: ItemStatus
    status_reason: str | None


@dataclass(frozen=True)
class ChangeSet:
    id: uuid.UUID
    workspace_id: uuid.UUID
    origin: str
    scope: dict[str, Any]
    title: str
    conversation_id: uuid.UUID | None
    created_by: uuid.UUID | None
    status: str
    created_at: datetime
    applied_by: uuid.UUID | None
    applied_at: datetime | None
    item_counts: dict[str, int]
    """Items by status."""


@dataclass(frozen=True)
class ChangeSetDetail:
    change_set: ChangeSet
    items: list[ChangeSetItem]


@dataclass(frozen=True)
class ChangeSetPage:
    items: list[ChangeSet]
    """Newest first."""
    next_cursor: str | None


@dataclass(frozen=True)
class SkippedItem:
    item_id: uuid.UUID
    reason: Literal["stale", "depends_on_skipped"]
    detail: str


@dataclass(frozen=True)
class ApplyResult:
    """What one accept did."""

    change_set: ChangeSetDetail
    accepted: list[uuid.UUID]
    skipped: list[SkippedItem] = field(default_factory=list)
    """Stale items and the items that depend on them: not applied, reported."""
    needs_owner: list[uuid.UUID] = field(default_factory=list)
    """Owner-only items (and what depends on them) still waiting for an owner."""


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Change Set not found.")


def _closed() -> ApiError:
    return ApiError(
        409, "change_set_closed", "This Change Set has already been applied, rejected or replaced."
    )


def _invalid(message: str, **details: Any) -> ApiError:
    return ApiError(422, "invalid_change_set", message, details or None)


def _item_view(record: ChangeSetItemRecord) -> ChangeSetItem:
    return ChangeSetItem(
        id=record.id,
        position=record.position,
        object_type=record.object_type,
        object_id=record.object_id,
        operation=record.operation,
        label=record.label,
        base_values=record.base_values,
        payload=record.payload,
        depends_on=[uuid.UUID(d) for d in record.depends_on],
        required_role=record.required_role,
        is_conflict=record.is_conflict,
        status=record.status,  # type: ignore[arg-type]  # constrained in the database
        status_reason=record.status_reason,
    )


def _view(record: ChangeSetRecord, counts: dict[str, int]) -> ChangeSet:
    return ChangeSet(
        id=record.id,
        workspace_id=record.workspace_id,
        origin=record.origin,
        scope=record.scope,
        title=record.title,
        conversation_id=record.conversation_id,
        created_by=record.created_by,
        status=record.status,
        created_at=record.created_at,
        applied_by=record.applied_by,
        applied_at=record.applied_at,
        item_counts=counts,
    )


def _counts(items: Sequence[ChangeSetItemRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _close_state(items: Sequence[ChangeSetItemRecord]) -> str:
    """The Change Set status the items imply: still ``pending`` while any item awaits a
    decision."""
    if any(i.status in OPEN_STATUSES for i in items):
        return "pending"
    accepted = sum(1 for i in items if i.status == "accepted")
    if accepted == 0:
        return "rejected"
    return "applied" if accepted == len(items) else "partially_applied"


def _dependents(items: Sequence[ChangeSetItemRecord], roots: set[str]) -> set[str]:
    """``roots`` plus every item that depends on one of them, transitively. Items are in
    position order and depend only on earlier ones, so one pass is enough."""
    found = set(roots)
    for item in items:
        if any(d in found for d in item.depends_on):
            found.add(str(item.id))
    return found


def _dependencies(items: Sequence[ChangeSetItemRecord], roots: set[str]) -> set[str]:
    """``roots`` plus everything they depend on, transitively."""
    by_id = {str(i.id): i for i in items}
    found: set[str] = set()
    stack = list(roots)
    while stack:
        item_id = stack.pop()
        if item_id in found:
            continue
        found.add(item_id)
        stack.extend(by_id[item_id].depends_on)
    return found


def reject_pending_change_sets(db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
    """Reject every pending Change Set of a Workspace, in ``db``'s transaction. It is the
    ``WorkspaceArchivedHook`` the composition root hands to the workspaces module."""
    ids = list(
        db.scalars(
            sa.select(ChangeSetRecord.id)
            .where(
                ChangeSetRecord.workspace_id == workspace_id, ChangeSetRecord.status == "pending"
            )
            .with_for_update()
        )
    )
    if not ids:
        return
    db.execute(
        sa.update(ChangeSetItemRecord)
        .where(
            ChangeSetItemRecord.change_set_id.in_(ids),
            ChangeSetItemRecord.status.in_(OPEN_STATUSES),
        )
        .values(status="rejected", status_reason="The Workspace was archived.")
    )
    db.execute(
        sa.update(ChangeSetRecord)
        .where(ChangeSetRecord.id.in_(ids))
        .values(status="rejected", applied_at=at)
    )


class ChangeSetService:
    """Every method authorizes through the workspaces policy first; callers add no checks
    of their own."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        handlers: ObjectHandlers,
        notifications: NotificationService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._handlers = handlers
        self._notifications = notifications
        self._clock = clock

    # -- proposing ------------------------------------------------------------------

    def propose(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        origin: str,
        scope: Mapping[str, Any],
        title: str,
        items: Sequence[ProposedItem],
        conversation_id: uuid.UUID | None = None,
    ) -> ChangeSetDetail:
        """Create a pending Change Set. A pending one of the same ``origin`` and ``scope``
        is superseded (its open items expire). Each item's required role comes from the
        policy action its handler names; an update's base values are read now. 422
        ``invalid_change_set`` for a malformed proposal (an unknown dependency, an object
        that does not exist, a value the handler refuses)."""
        self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        if origin not in ORIGINS:
            raise ValueError(f"unknown Change Set origin {origin!r}")
        title = title.strip()[:TITLE_MAX_LENGTH]
        if not items:
            raise _invalid("A Change Set needs at least one item.")
        if len(items) > MAX_ITEMS:
            raise _invalid(f"A Change Set has at most {MAX_ITEMS} items.")
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            ids: dict[str, uuid.UUID] = {}
            records: list[ChangeSetItemRecord] = []
            change_set_id = uuid.uuid4()
            for position, proposed in enumerate(items):
                if proposed.key in ids:
                    raise _invalid(f"Two items share the key `{proposed.key}`.")
                records.append(
                    self._item_record(db, workspace_id, change_set_id, position, proposed, ids)
                )
                ids[proposed.key] = records[-1].id
            self._supersede(db, workspace_id, origin, scope)
            record = ChangeSetRecord(
                id=change_set_id,
                workspace_id=workspace_id,
                origin=origin,
                scope=dict(scope),
                title=title,
                conversation_id=conversation_id,
                created_by=user.id,
                status="pending",
                created_at=now,
            )
            db.add(record)
            db.flush()
            db.add_all(records)
            db.flush()
            return ChangeSetDetail(
                _view(record, _counts(records)), [_item_view(r) for r in records]
            )

    def _item_record(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        change_set_id: uuid.UUID,
        position: int,
        proposed: ProposedItem,
        earlier: Mapping[str, uuid.UUID],
    ) -> ChangeSetItemRecord:
        if proposed.operation not in OPERATIONS:
            raise _invalid(f"Unknown operation `{proposed.operation}`.", item=proposed.key)
        handler = self._handlers.get(proposed.object_type)
        if proposed.operation != "create" and proposed.object_id is None:
            raise _invalid("An update or delete names the object it changes.", item=proposed.key)
        unknown = [k for k in proposed.depends_on if k not in earlier]
        if unknown:
            raise _invalid(
                f"Item `{proposed.key}` depends on {unknown}, which are not earlier items.",
                item=proposed.key,
            )
        try:
            role = required_role(handler.required_action(proposed.operation))
        except ValueError as exc:
            raise _invalid(str(exc), item=proposed.key) from None
        handler.validate(db, workspace_id, proposed.operation, proposed.object_id, proposed.payload)
        base_values = dict(proposed.base_values) if proposed.base_values is not None else None
        if proposed.operation != "create" and proposed.object_id is not None:
            fields = list(base_values if base_values is not None else proposed.payload)
            current = handler.current_values(db, workspace_id, proposed.object_id, fields)
            if current is None:
                raise _invalid(
                    f"`{proposed.label or proposed.key}` does not exist.", item=proposed.key
                )
            if base_values is None and proposed.operation == "update":
                base_values = current
        return ChangeSetItemRecord(
            id=uuid.uuid4(),
            change_set_id=change_set_id,
            position=position,
            object_type=proposed.object_type,
            object_id=proposed.object_id,
            operation=proposed.operation,
            label=proposed.label[:LABEL_MAX_LENGTH],
            base_values=base_values,
            payload=dict(proposed.payload),
            depends_on=[str(earlier[k]) for k in proposed.depends_on],
            required_role=role,
            is_conflict=proposed.is_conflict,
            status="pending",
            status_reason=None,
        )

    def _supersede(
        self, db: Session, workspace_id: uuid.UUID, origin: str, scope: Mapping[str, Any]
    ) -> None:
        older = list(
            db.scalars(
                sa.select(ChangeSetRecord.id)
                .where(
                    ChangeSetRecord.workspace_id == workspace_id,
                    ChangeSetRecord.origin == origin,
                    ChangeSetRecord.scope == dict(scope),
                    ChangeSetRecord.status == "pending",
                )
                .with_for_update()
            )
        )
        if not older:
            return
        db.execute(
            sa.update(ChangeSetItemRecord)
            .where(
                ChangeSetItemRecord.change_set_id.in_(older),
                ChangeSetItemRecord.status.in_(OPEN_STATUSES),
            )
            .values(status="expired", status_reason="A newer Change Set replaced this one.")
        )
        db.execute(
            sa.update(ChangeSetRecord)
            .where(ChangeSetRecord.id.in_(older))
            .values(status="superseded")
        )

    # -- reading --------------------------------------------------------------------

    def get(self, user: User, workspace_id: uuid.UUID, change_set_id: uuid.UUID) -> ChangeSetDetail:
        """A Change Set with its items (any member). 404 if it is not in the Workspace."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = self._load(db, workspace_id, change_set_id)
            return self._detail(db, record)

    def list(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        status: str | None = None,
        conversation_id: uuid.UUID | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> ChangeSetPage:
        """The Workspace's Change Sets, newest first, optionally of one status or from one
        assistant conversation (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        if status is not None and status not in CHANGE_SET_STATUSES:
            raise ApiError(422, "invalid_filter", f"Unknown status `{status}`.")
        query = (
            sa.select(ChangeSetRecord)
            .where(ChangeSetRecord.workspace_id == workspace_id)
            .order_by(ChangeSetRecord.created_at.desc(), ChangeSetRecord.id.desc())
            .limit(limit + 1)
        )
        if status is not None:
            query = query.where(ChangeSetRecord.status == status)
        if conversation_id is not None:
            query = query.where(ChangeSetRecord.conversation_id == conversation_id)
        if cursor is not None:
            after_at, after_id = decode_cursor(cursor, 2)
            try:
                at = datetime.fromisoformat(after_at)
                after_uuid = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.") from None
            if at.tzinfo is None:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.")
            query = query.where(
                sa.tuple_(ChangeSetRecord.created_at, ChangeSetRecord.id)
                < sa.tuple_(
                    sa.literal(at, sa.DateTime(timezone=True)), sa.literal(after_uuid, sa.Uuid)
                )
            )
        with Session(self._engine) as db:
            records = list(db.scalars(query))
            page = records[:limit]
            counts = self._counts_for(db, [r.id for r in page])
        next_cursor = None
        if len(records) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))
        return ChangeSetPage(
            items=[_view(r, counts.get(r.id, {})) for r in page], next_cursor=next_cursor
        )

    def _counts_for(self, db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
        counts: dict[uuid.UUID, dict[str, int]] = {}
        rows = db.execute(
            sa.select(
                ChangeSetItemRecord.change_set_id,
                ChangeSetItemRecord.status,
                sa.func.count(),
            )
            .where(ChangeSetItemRecord.change_set_id.in_(ids))
            .group_by(ChangeSetItemRecord.change_set_id, ChangeSetItemRecord.status)
        )
        for change_set_id, status, count in rows:
            counts.setdefault(change_set_id, {})[status] = count
        return counts

    def _load(
        self, db: Session, workspace_id: uuid.UUID, change_set_id: uuid.UUID, *, lock: bool = False
    ) -> ChangeSetRecord:
        query = sa.select(ChangeSetRecord).where(
            ChangeSetRecord.id == change_set_id, ChangeSetRecord.workspace_id == workspace_id
        )
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        if record is None:
            raise _not_found()
        return record

    def _items(self, db: Session, change_set_id: uuid.UUID) -> list[ChangeSetItemRecord]:
        return list(
            db.scalars(
                sa.select(ChangeSetItemRecord)
                .where(ChangeSetItemRecord.change_set_id == change_set_id)
                .order_by(ChangeSetItemRecord.position)
            )
        )

    def _detail(self, db: Session, record: ChangeSetRecord) -> ChangeSetDetail:
        items = self._items(db, record.id)
        return ChangeSetDetail(_view(record, _counts(items)), [_item_view(i) for i in items])

    # -- deciding -------------------------------------------------------------------

    def accept(
        self,
        user: User,
        workspace_id: uuid.UUID,
        change_set_id: uuid.UUID,
        item_ids: Sequence[uuid.UUID] | None = None,
    ) -> ApplyResult:
        """Accept ``item_ids`` (``None``: every open item) with what they depend on, and
        apply them all in one transaction, audited as ``user``. Stale items and their
        dependents are skipped and reported; owner-only items stay ``needs_owner`` unless
        ``user`` is an owner (owners are notified); unselected items expire, which closes
        the Change Set unless an owner still has something to decide. 409
        ``change_set_closed`` if it already is; 422 ``invalid_change_set`` for an item
        that is not an open item of it."""
        scope = self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        is_owner = scope.role == "owner"
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, change_set_id, lock=True)
            if record.status != "pending":
                raise _closed()
            items = self._items(db, record.id)
            by_id = {str(i.id): i for i in items}
            selected = self._selection(items, item_ids)
            selected = {
                i for i in _dependencies(items, selected) if by_id[i].status in OPEN_STATUSES
            }

            owner_only = {i for i in selected if by_id[i].required_role == "owner" and not is_owner}
            waiting = _dependents(items, owner_only) & selected
            candidates = [i for i in items if str(i.id) in selected - waiting]

            skipped = self._skip_stale(db, workspace_id, candidates, by_id)
            accepted: list[uuid.UUID] = []
            for item in candidates:
                if str(item.id) in skipped:
                    continue
                self._apply_item(db, user, record, item, now)
                item.status, item.status_reason = "accepted", None
                accepted.append(item.id)
            for item_id in skipped:
                by_id[item_id].status = "stale"
                by_id[item_id].status_reason = skipped[item_id].detail[:REASON_MAX_LENGTH]

            for item_id in waiting:
                by_id[item_id].status = "needs_owner" if item_id in owner_only else "pending"
            for item in items:
                if item.status in OPEN_STATUSES and str(item.id) not in waiting:
                    item.status = "expired"
                    item.status_reason = "Closed without a decision."
            record.status = _close_state(items)
            if record.status != "pending" or accepted:
                record.applied_by, record.applied_at = user.id, now
            if accepted:
                record_activity(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    verb="change_set.applied",
                    object_type="change_set",
                    object_id=record.id,
                    object_label=record.title,
                    details={"accepted": len(accepted), "skipped": len(skipped)},
                    at=now,
                )
            if owner_only:
                self._notifications.notify(
                    self._workspaces.owner_ids(workspace_id),
                    kind="needs_owner",
                    message=(
                        f'{len(owner_only)} item(s) of the Change Set "{record.title}" '
                        "need an owner to accept them."
                    )[:500],
                    workspace_id=workspace_id,
                    ref_type="change_set",
                    ref_id=record.id,
                    db=db,
                )
            db.flush()
            return ApplyResult(
                change_set=self._detail(db, record),
                accepted=accepted,
                skipped=list(skipped.values()),
                needs_owner=[i.id for i in items if str(i.id) in waiting],
            )

    def reject(
        self,
        user: User,
        workspace_id: uuid.UUID,
        change_set_id: uuid.UUID,
        item_ids: Sequence[uuid.UUID] | None = None,
    ) -> ChangeSetDetail:
        """Reject ``item_ids`` (``None``: every open item) and the items that depend on
        them. The Change Set closes, expiring any item left open, once nothing awaits a
        decision. 409 ``change_set_closed``."""
        self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, change_set_id, lock=True)
            if record.status != "pending":
                raise _closed()
            items = self._items(db, record.id)
            by_id = {str(i.id): i for i in items}
            selected = self._selection(items, item_ids)
            for item_id in _dependents(items, selected):
                item = by_id[item_id]
                if item.status in OPEN_STATUSES:
                    item.status = "rejected"
                    item.status_reason = (
                        "Rejected." if item_id in selected else "Depends on a rejected item."
                    )
            record.status = _close_state(items)
            if record.status != "pending":
                record.applied_by, record.applied_at = user.id, now
            db.flush()
            return self._detail(db, record)

    def _selection(
        self, items: Sequence[ChangeSetItemRecord], item_ids: Sequence[uuid.UUID] | None
    ) -> set[str]:
        open_ids = {str(i.id) for i in items if i.status in OPEN_STATUSES}
        if item_ids is None:
            return open_ids
        wanted = {str(i) for i in item_ids}
        if not wanted <= open_ids:
            raise _invalid(
                "Some items are not open items of this Change Set.",
                items=sorted(wanted - open_ids),
            )
        return wanted

    def _skip_stale(
        self,
        db: Session,
        workspace_id: uuid.UUID,
        candidates: Sequence[ChangeSetItemRecord],
        by_id: Mapping[str, ChangeSetItemRecord],
    ) -> dict[str, SkippedItem]:
        """The candidates that must not be applied: stale ones (a field they change has
        changed since) and what depends on them. Staleness is per changed field only."""
        skipped: dict[str, SkippedItem] = {}
        for item in candidates:
            item_id = str(item.id)
            blocking = [d for d in item.depends_on if d in skipped]
            if blocking:
                skipped[item_id] = SkippedItem(
                    item.id, "depends_on_skipped", "Depends on an item that is stale."
                )
                continue
            if any(by_id[d].status not in ("accepted", *OPEN_STATUSES) for d in item.depends_on):
                skipped[item_id] = SkippedItem(
                    item.id, "depends_on_skipped", "Depends on an item that was not applied."
                )
                continue
            if item.base_values is None or item.object_id is None:
                continue
            handler = self._handlers.get(item.object_type)
            current = handler.current_values(
                db, workspace_id, item.object_id, list(item.base_values)
            )
            if current is None:
                skipped[item_id] = SkippedItem(item.id, "stale", "The object no longer exists.")
                continue
            changed = [f for f, v in item.base_values.items() if current.get(f) != v]
            if changed:
                skipped[item_id] = SkippedItem(
                    item.id, "stale", f"Changed since it was proposed: {', '.join(changed)}."
                )
        return skipped

    def _apply_item(
        self,
        db: Session,
        user: User,
        record: ChangeSetRecord,
        item: ChangeSetItemRecord,
        now: datetime,
    ) -> None:
        handler = self._handlers.get(item.object_type)
        applied = handler.apply(
            db, record.workspace_id, item.operation, item.object_id, item.payload, at=now
        )
        record_audit(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            entity_type=applied.entity_type,
            entity_id=applied.entity_id,
            old=applied.old,
            new=applied.new,
            at=now,
            via=record.origin,
            change_set_id=record.id,
        )
