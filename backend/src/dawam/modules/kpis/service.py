from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.sources import SourceSystemService
from dawam.modules.warehouse import (
    DwSchema,
    SchemaColumn,
    read_mapping_state,
    read_schema,
)
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor
from dawam.platform.pii_validators import redact_text

from .links import (
    check_links,
    described,
    drop_links,
    link_ids,
    restore_links,
    validate_formula,
    write_links,
)
from .suggestions import (
    COMPUTABLE,
    NEEDS_DATA,
    RuleSuggestion,
    feasibility_of,
    formula_columns,
    suggest,
)
from .tables import (
    AGGREGATION_MAX_LENGTH,
    CATALOG_STATES,
    MAX_TARGETS,
    NAME_MAX_LENGTH,
    OWNER_MAX_LENGTH,
    REFRESH_MAX_LENGTH,
    SQL_MAX_LENGTH,
    STATUSES,
    TARGET_TEXT_MAX_LENGTH,
    TEXT_MAX_LENGTH,
    UNIT_MAX_LENGTH,
    KpiLinkRecord,
    KpiRecord,
    KpiSuggestionDismissalRecord,
)

AUDITED_FIELDS = (
    "source_system_id",
    "name",
    "definition",
    "formula_text",
    "formula_sql",
    "unit",
    "aggregation",
    "owner",
    "refresh_frequency",
    "targets",
    "status",
)
"""The fields of a KPI the audit trail records (all of them but bookkeeping)."""

_TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "definition": ("business definition", TEXT_MAX_LENGTH),
    "formula_text": ("formula in words", TEXT_MAX_LENGTH),
    "unit": ("unit", UNIT_MAX_LENGTH),
    "aggregation": ("aggregation", AGGREGATION_MAX_LENGTH),
    "owner": ("owner", OWNER_MAX_LENGTH),
    "refresh_frequency": ("refresh frequency", REFRESH_MAX_LENGTH),
}


@dataclass(frozen=True)
class KpiTarget:
    label: str
    value: str


@dataclass(frozen=True)
class Kpi:
    id: uuid.UUID
    workspace_id: uuid.UUID
    source_system_id: uuid.UUID | None
    """``None``: a Data Warehouse KPI."""
    name: str
    definition: str
    formula_text: str
    formula_sql: str | None
    unit: str
    aggregation: str
    owner: str
    refresh_frequency: str
    targets: list[KpiTarget]
    origin: str
    rationale: str | None
    """Why the AI suggested it (``origin == "ai"``); ``None`` otherwise."""
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    review_state: str = "live"
    """``live``, ``suggested``, ``accepted`` or ``rejected``; apart from ``status``."""
    decision: str | None = None
    """``added`` (live), ``accepted``, ``rejected`` or ``removed``; ``None`` while suggested."""
    decided_by: uuid.UUID | None = None
    """Who added the KPI by hand or decided on the suggestion."""
    decided_at: datetime | None = None
    link_count: int = 0
    """How many DW columns the KPI links to."""
    feasibility: str | None = None
    """``computable``, ``needs_mapping`` or ``needs_data``; ``None`` without links."""


@dataclass(frozen=True)
class KpiPage:
    items: list[Kpi]
    next_cursor: str | None
    rule_suggestions: list[RuleSuggestion] = field(default_factory=list)
    """Open rule-based suggestions (first page, when asked for)."""
    rejected_rule_suggestions: list[RejectedRuleSuggestion] = field(default_factory=list)
    """Dismissed rule-based suggestions (first page, when asked for)."""


@dataclass(frozen=True)
class RejectedRuleSuggestion:
    suggestion: RuleSuggestion
    decided_by: uuid.UUID | None
    decided_at: datetime


@dataclass(frozen=True)
class KpiLink:
    dw_column_id: uuid.UUID
    table_id: uuid.UUID
    layer: str
    table_name: str
    column_name: str
    role: str


@dataclass(frozen=True)
class KpiLinks:
    """A KPI's links, with the KPI's ``version`` to send back with the next edit."""

    version: int
    items: list[KpiLink]


@dataclass(frozen=True)
class LinkCandidates:
    """What the AI needs to propose links and formula SQL."""

    kpis: list[Kpi]
    """KPIs with no links or no formula SQL (at most ``MAX_CANDIDATES``)."""
    schema: DwSchema | None
    """The Core and Mart tables; ``None`` before the Data Warehouse is set up."""


MAX_CANDIDATES = 100


@dataclass(frozen=True)
class KpiFeasibility:
    """Whether a KPI can be computed from the DW Schema (spec §6.13)."""

    feasibility: str
    """``computable``, ``needs_mapping`` or ``needs_data``."""
    unmapped: list[str]
    """``table.column`` of the inputs no mapping fills yet."""


def _link_view(column: SchemaColumn) -> KpiLink:
    return KpiLink(
        dw_column_id=column.id,
        table_id=column.table_id,
        layer=column.layer,
        table_name=column.table_name,
        column_name=column.name,
        role=column.role,
    )


@dataclass(frozen=True)
class KpiSuggestion:
    """One KPI the AI proposes."""

    name: str
    rationale: str
    definition: str = ""
    formula_text: str = ""
    unit: str = ""
    aggregation: str = ""


@dataclass(frozen=True)
class SuggestedKpis:
    created: list[Kpi]
    skipped: list[str]
    """Names of suggestions left out because a KPI of that name already exists."""


def _name_key(name: str) -> str:
    return " ".join(name.lower().split())


def _decision(record: KpiRecord) -> tuple[str | None, uuid.UUID | None, datetime | None]:
    if record.review_state == "live":
        return "added", record.created_by, record.created_at
    if record.review_state == "suggested":
        return None, None, None
    if record.review_state == "rejected" and record.removed:
        return "removed", record.decided_by, record.decided_at
    return record.review_state, record.decided_by, record.decided_at


def _view(record: KpiRecord, *, link_count: int = 0, feasibility: str | None = None) -> Kpi:
    decision, decided_by, decided_at = _decision(record)
    return Kpi(
        review_state=record.review_state,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at,
        link_count=link_count,
        feasibility=feasibility,
        id=record.id,
        workspace_id=record.workspace_id,
        source_system_id=record.source_system_id,
        name=record.name,
        definition=record.definition,
        formula_text=record.formula_text,
        formula_sql=record.formula_sql,
        unit=record.unit,
        aggregation=record.aggregation,
        owner=record.owner,
        refresh_frequency=record.refresh_frequency,
        targets=[KpiTarget(label=t["label"], value=t["value"]) for t in record.targets],
        origin=record.origin,
        rationale=record.rationale,
        status=record.status,
        version=record.version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _snapshot(record: KpiRecord) -> dict[str, Any]:
    """The audited fields as JSON."""
    return {
        field: str(value) if isinstance(value := getattr(record, field), uuid.UUID) else value
        for field in AUDITED_FIELDS
    }


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "KPI not found.")


def _not_in_catalog(record: KpiRecord, message: str) -> ApiError:
    return ApiError(409, "invalid_review_state", message, {"review_state": record.review_state})


def _invalid(message: str, field: str) -> ApiError:
    return ApiError(422, "invalid_kpi", message, {"field": field})


def _clean(value: str, label: str, field: str, max_length: int, *, required: bool = False) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise _invalid(f"The {label} must not be empty.", field)
    if len(cleaned) > max_length:
        raise _invalid(f"The {label} must be at most {max_length} characters.", field)
    return cleaned


def _clean_targets(targets: Sequence[KpiTarget]) -> list[dict[str, str]]:
    if len(targets) > MAX_TARGETS:
        raise _invalid(f"A KPI has at most {MAX_TARGETS} targets.", "targets")
    return [
        {
            "label": _clean(
                t.label, "target label", "targets", TARGET_TEXT_MAX_LENGTH, required=True
            ),
            "value": _clean(
                t.value, "target value", "targets", TARGET_TEXT_MAX_LENGTH, required=True
            ),
        }
        for t in targets
    ]


def _clean_status(status: str) -> str:
    if status not in STATUSES:
        raise _invalid(f"The status must be one of {', '.join(STATUSES)}.", "status")
    return status


class KpiService:
    """KPIs of a Workspace. Every method that acts for a user authorizes through the
    workspaces module's policy first; callers add no checks of their own."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        systems: SourceSystemService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._systems = systems
        self._clock = clock

    def create(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        name: str,
        source_system_id: uuid.UUID | None = None,
        definition: str = "",
        formula_text: str = "",
        formula_sql: str | None = None,
        unit: str = "",
        aggregation: str = "",
        owner: str = "",
        refresh_frequency: str = "",
        targets: Sequence[KpiTarget] = (),
    ) -> Kpi:
        """Document a KPI as a draft, under a Source System or (``source_system_id``
        ``None``) the Data Warehouse, which needs no setup. 404 if the Source System is
        not in the Workspace; 422 ``invalid_kpi``."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        if source_system_id is not None:
            self._systems.get(user, workspace_id, source_system_id)
        cleaned_sql = _clean_sql(formula_sql)
        if cleaned_sql is not None:
            with Session(self._engine) as db:
                validate_formula(read_schema(db, workspace_id), cleaned_sql)
        now = self._clock()
        record = KpiRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            source_system_id=source_system_id,
            name=_clean(name, "name", "name", NAME_MAX_LENGTH, required=True),
            origin="user",
            status="draft",
            created_by=user.id,
            created_at=now,
            updated_at=now,
            version=1,
            targets=_clean_targets(targets),
            formula_sql=cleaned_sql,
            **{
                field: _clean(value, label, field, limit)
                for field, value, (label, limit) in (
                    (f, v, _TEXT_FIELDS[f])
                    for f, v in (
                        ("definition", definition),
                        ("formula_text", formula_text),
                        ("unit", unit),
                        ("aggregation", aggregation),
                        ("owner", owner),
                        ("refresh_frequency", refresh_frequency),
                    )
                )
            },
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            db.flush()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old=None,
                new=_snapshot(record),
                at=now,
            )
            self._record_activity(db, user, "kpi.created", record)
            return _view(record)

    def suggest(
        self,
        user: User,
        workspace_id: uuid.UUID,
        suggestions: Sequence[KpiSuggestion],
        *,
        source_system_id: uuid.UUID | None = None,
    ) -> SuggestedKpis:
        """Create the AI's suggestions as ``suggested`` KPIs (``origin`` ``ai``, with a
        rationale, status draft) waiting for an owner or editor to accept or reject them,
        under a Source System or the Data Warehouse. Create-only: a suggestion whose name
        matches a KPI of the same scope in any review state (rejected included; ignoring
        case and spaces), or an earlier suggestion, is skipped; no KPI is ever updated or
        deleted. One audit entry ``via=ai`` per KPI. Every text is checked with the PII
        validators and matches are redacted. 404 if the Source System is not in the
        Workspace; 422 ``invalid_kpi`` (nothing is created)."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        if source_system_id is not None:
            self._systems.get(user, workspace_id, source_system_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            taken = {
                _name_key(name)
                for name in db.scalars(
                    sa.select(KpiRecord.name).where(
                        KpiRecord.workspace_id == workspace_id,
                        KpiRecord.source_system_id.is_(None)
                        if source_system_id is None
                        else KpiRecord.source_system_id == source_system_id,
                    )
                )
            }
            created: list[KpiRecord] = []
            skipped: list[str] = []
            for suggestion in suggestions:
                name = _clean(
                    redact_text(suggestion.name), "name", "name", NAME_MAX_LENGTH, required=True
                )
                if _name_key(name) in taken:
                    skipped.append(name)
                    continue
                taken.add(_name_key(name))
                texts = {
                    field: _clean(redact_text(value), label, field, limit)
                    for field, value in (
                        ("definition", suggestion.definition),
                        ("formula_text", suggestion.formula_text),
                        ("unit", suggestion.unit),
                        ("aggregation", suggestion.aggregation),
                    )
                    for label, limit in (_TEXT_FIELDS[field],)
                }
                record = KpiRecord(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    source_system_id=source_system_id,
                    name=name,
                    owner="",
                    refresh_frequency="",
                    targets=[],
                    formula_sql=None,
                    origin="ai",
                    rationale=_clean(
                        redact_text(suggestion.rationale), "rationale", "rationale", TEXT_MAX_LENGTH
                    )
                    or None,
                    status="draft",
                    review_state="suggested",
                    created_by=user.id,
                    created_at=now,
                    updated_at=now,
                    version=1,
                    **texts,
                )
                db.add(record)
                created.append(record)
            db.flush()
            for record in created:
                record_audit(
                    db,
                    workspace_id=workspace_id,
                    actor_id=user.id,
                    entity_type="kpi",
                    entity_id=record.id,
                    old=None,
                    new={
                        **_snapshot(record),
                        "origin": "ai",
                        "rationale": record.rationale,
                        "review_state": "suggested",
                    },
                    at=now,
                    via="ai",
                )
                self._record_activity(db, user, "kpi.created", record, details={"via": "ai"})
            return SuggestedKpis(created=[_view(r) for r in created], skipped=skipped)

    def rule_suggestions(self, user: User, workspace_id: uuid.UUID) -> list[RuleSuggestion]:
        """The rule-based KPI suggestions for the DW Schema (any member): a computed list,
        not KPIs. Left out are the dismissed ones and those whose name is already a KPI's
        in the KPIs tab (an accepted one). Empty before the Data Warehouse is set up."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return self._open_suggestions(db, workspace_id)[0]

    def accept_suggestion(self, user: User, workspace_id: uuid.UUID, key: str) -> Kpi:
        """Accept a rule-based suggestion: a draft Data Warehouse KPI (``origin`` ``rule``,
        review state ``accepted``, the decision recorded) with its formula SQL and links to
        the measures, audited like any new KPI. A dismissed suggestion can be accepted after
        all: the dismissal is cleared. 404 if no such suggestion is open (it was accepted or
        is gone)."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            open_ones, schema = self._open_suggestions(db, workspace_id, include_dismissed=True)
            suggestion = next((s for s in open_ones if s.key == key), None)
            if suggestion is None or schema is None:
                raise ApiError(404, "not_found", "Suggestion not found.")
            validate_formula(schema, suggestion.formula_sql)
            record = KpiRecord(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                source_system_id=None,
                name=suggestion.name,
                definition=suggestion.definition,
                formula_text=suggestion.formula_text,
                formula_sql=suggestion.formula_sql,
                unit="",
                aggregation=suggestion.aggregation,
                owner="",
                refresh_frequency="",
                targets=[],
                origin="rule",
                rationale=None,
                status="draft",
                review_state="accepted",
                suggestion_key=key,
                decided_by=user.id,
                decided_at=now,
                created_by=user.id,
                created_at=now,
                updated_at=now,
                version=1,
            )
            db.add(record)
            dismissal = db.get(KpiSuggestionDismissalRecord, (workspace_id, key))
            if dismissal is not None:
                db.delete(dismissal)
            db.flush()
            write_links(db, record.id, suggestion.column_ids)
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old=None,
                new={
                    **_snapshot(record),
                    "origin": "rule",
                    "links": sorted(str(i) for i in suggestion.column_ids),
                },
                at=now,
            )
            self._record_activity(db, user, "kpi.created", record, details={"origin": "rule"})
            return _views(db, workspace_id, [record])[0]

    def dismiss_suggestion(self, user: User, workspace_id: uuid.UUID, key: str) -> None:
        """Hide a rule-based suggestion for good (owners and editors); dismissing one that
        is already hidden is fine. 404 if there is no such suggestion."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            schema = read_schema(db, workspace_id)
            known = schema is not None and any(
                s.key == key for s in suggest(schema, read_mapping_state(db, workspace_id))
            )
            if not known:
                raise ApiError(404, "not_found", "Suggestion not found.")
            if db.get(KpiSuggestionDismissalRecord, (workspace_id, key)) is None:
                db.add(
                    KpiSuggestionDismissalRecord(
                        workspace_id=workspace_id,
                        key=key,
                        dismissed_by=user.id,
                        dismissed_at=self._clock(),
                    )
                )

    def feasibility(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> KpiFeasibility:
        """Whether a KPI (a rule or an AI suggestion, or any other) can be computed (any
        member): *computable* when the DW columns its links and formula SQL read exist and
        are mapped, *needs mapping* when some are unmapped, *needs data* when it has none
        or the formula names what the DW Schema lacks."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = self._load_visible(db, user, workspace_id, kpi_id)
            schema = read_schema(db, workspace_id)
            if schema is None:
                return KpiFeasibility(NEEDS_DATA, [])
            return _feasibility(
                schema,
                read_mapping_state(db, workspace_id).mapped,
                set(link_ids(db, kpi_id)),
                record.formula_sql,
            )

    def list(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        source_system_id: uuid.UUID | None = None,
        data_warehouse: bool = False,
        review: str = "catalog",
        rule_suggestions: bool = False,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> KpiPage:
        """The Workspace's KPIs (any member), by name; narrowed to one Source System's or
        to the Data Warehouse's. ``review`` ``catalog``: the KPIs tab (live and accepted);
        ``all``: suggested and rejected KPIs too (owners and editors, ``kpi.view_review``).
        ``rule_suggestions`` (with ``review`` ``all``) adds the open and the dismissed
        rule-based suggestions to the first page, so the Data Warehouse page needs one
        request. 422 ``invalid_filter`` if both scopes are asked for, or the suggestions
        without ``review`` ``all``."""
        self._workspaces.authorize(
            user,
            Action.VIEW_KPI_REVIEW if review == "all" else Action.VIEW_WORKSPACE,
            workspace_id,
        )
        if rule_suggestions and review != "all":
            raise ApiError(422, "invalid_filter", "Rule suggestions come with `review=all` only.")
        if source_system_id is not None and data_warehouse:
            raise ApiError(
                422,
                "invalid_filter",
                "Ask for one Source System's KPIs or the Data Warehouse's, not both.",
            )
        if source_system_id is not None:
            self._systems.get(user, workspace_id, source_system_id)
        query = (
            sa.select(KpiRecord)
            .where(KpiRecord.workspace_id == workspace_id)
            .order_by(KpiRecord.name, KpiRecord.id)
            .limit(limit + 1)
        )
        if review != "all":
            query = query.where(KpiRecord.review_state.in_(CATALOG_STATES))
        if source_system_id is not None:
            query = query.where(KpiRecord.source_system_id == source_system_id)
        elif data_warehouse:
            query = query.where(KpiRecord.source_system_id.is_(None))
        if cursor is not None:
            after_name, after_id = decode_cursor(cursor, 2)
            try:
                after = uuid.UUID(after_id)
            except ValueError:
                raise ApiError(
                    422, "invalid_cursor", "The cursor is not valid; start from the first page."
                ) from None
            query = query.where(sa.tuple_(KpiRecord.name, KpiRecord.id) > (after_name, after))
        with Session(self._engine) as db:
            records = list(db.scalars(query))
            page = records[:limit]
            items = _views(db, workspace_id, page)
            open_ones: list[RuleSuggestion] = []
            rejected: list[RejectedRuleSuggestion] = []
            if rule_suggestions and cursor is None:
                open_ones = self._open_suggestions(db, workspace_id)[0]
                rejected = self._rejected_suggestions(db, workspace_id)
        next_cursor = (
            encode_cursor(page[-1].name, str(page[-1].id)) if len(records) > limit else None
        )
        return KpiPage(
            items=items,
            next_cursor=next_cursor,
            rule_suggestions=open_ones,
            rejected_rule_suggestions=rejected,
        )

    def get(
        self,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        *,
        catalog_only: bool = False,
    ) -> Kpi:
        """Open a KPI (any member). A suggested or rejected one opens only for owners and
        editors (``kpi.view_review``), and never with ``catalog_only`` (the assistant's
        reads). 404 if it is not in ``workspace_id``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = self._load_visible(db, user, workspace_id, kpi_id, catalog_only=catalog_only)
            return _views(db, workspace_id, [record])[0]

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        *,
        version: int,
        changes: Mapping[str, Any],
    ) -> Kpi:
        """Edit a KPI. ``changes`` holds only the fields to change (any of ``name``,
        ``definition``, ``formula_text``, ``formula_sql`` (``None`` clears it), ``unit``,
        ``aggregation``, ``owner``, ``refresh_frequency``, ``targets`` (a list of
        ``KpiTarget``) and ``status``); the others stay. A stale ``version`` is 409
        ``version_conflict``; a suggested or rejected KPI is 409 ``invalid_review_state``
        (accept it first)."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            if record.review_state not in CATALOG_STATES:
                raise _not_in_catalog(record, "Accept this KPI before editing it.")
            if record.version != version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "Someone else changed this KPI since you loaded it. Reload and try again.",
                    {"current_version": record.version},
                )
            before = _snapshot(record)
            for field, value in changes.items():
                if field == "name":
                    record.name = _clean(value, "name", "name", NAME_MAX_LENGTH, required=True)
                elif field in _TEXT_FIELDS:
                    label, limit = _TEXT_FIELDS[field]
                    setattr(record, field, _clean(value, label, field, limit))
                elif field == "formula_sql":
                    record.formula_sql = _clean_sql(value)
                    if record.formula_sql is not None:
                        validate_formula(read_schema(db, workspace_id), record.formula_sql)
                elif field == "targets":
                    record.targets = _clean_targets(value)
                elif field == "status":
                    record.status = _clean_status(value)
                else:  # pragma: no cover - a programming error
                    raise ValueError(f"cannot change KPI field {field!r}")
            _revoke_approval(record, before, changes)
            after = _snapshot(record)
            changed = [f for f in AUDITED_FIELDS if before[f] != after[f]]
            if not changed:
                return _views(db, workspace_id, [record])[0]
            record.version += 1
            now = self._clock()
            record.updated_at = now
            db.flush()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old={f: before[f] for f in changed},
                new={f: after[f] for f in changed},
                at=now,
            )
            self._record_activity(
                db,
                user,
                "kpi.status_changed" if changed == ["status"] else "kpi.edited",
                record,
                details={"fields": changed},
            )
            return _views(db, workspace_id, [record])[0]

    def delete(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> None:
        """Delete a KPI (owners and editors). One added by hand (or by a rule) is deleted,
        the audit trail keeping what it held. One that came from an AI suggestion is not:
        an accepted one moves to ``rejected`` as removed ("Removed by"), a suggested one is
        rejected, so the suggestion never comes back on its own."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            if record.review_state != "live":
                if record.review_state != "rejected":
                    self._decide(
                        db, user, record, "rejected", removed=record.review_state == "accepted"
                    )
                return
            now = self._clock()
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="kpi",
                entity_id=record.id,
                old=_snapshot(record),
                new=None,
                at=now,
            )
            self._record_activity(db, user, "kpi.deleted", record)
            drop_links(db, record.id)
            db.delete(record)

    def accept(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> Kpi:
        """Accept an AI-suggested KPI, or a rejected one after all (owners and editors): it
        joins the KPIs tab as ``accepted``, its status unchanged. Audited. 409
        ``invalid_review_state`` for a KPI that is not suggested or rejected."""
        return self._review(user, workspace_id, kpi_id, "accepted", ("suggested", "rejected"))

    def reject(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> Kpi:
        """Reject an AI-suggested KPI (owners and editors): it moves to ``rejected`` and
        counts nowhere else. Audited. 409 ``invalid_review_state`` unless it is suggested."""
        return self._review(user, workspace_id, kpi_id, "rejected", ("suggested",))

    def _review(
        self,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        state: str,
        allowed_from: tuple[str, ...],
    ) -> Kpi:
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            if record.review_state not in allowed_from:
                raise ApiError(
                    409,
                    "invalid_review_state",
                    f"Only a {' or '.join(allowed_from)} KPI can be {state}; "
                    f"this one is {record.review_state}.",
                    {"review_state": record.review_state},
                )
            self._decide(db, user, record, state)
            return _views(db, workspace_id, [record])[0]

    def _decide(
        self, db: Session, user: User, record: KpiRecord, state: str, *, removed: bool = False
    ) -> None:
        """Move the locked ``record`` to ``state`` as ``user``'s decision, audit it and keep
        its lineage edges only while it is in the KPIs tab (its links stay stored)."""
        before = {"review_state": record.review_state, "removed": record.removed}
        record.review_state = state
        record.removed = removed
        now = self._clock()
        record.decided_by = user.id
        record.decided_at = now
        record.version += 1
        record.updated_at = now
        db.flush()
        if state in CATALOG_STATES:
            restore_links(db, record.id)
        else:
            drop_links(db, record.id)
        record_audit(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            entity_type="kpi",
            entity_id=record.id,
            old=before,
            new={"review_state": state, "removed": removed},
            at=now,
        )
        if record.suggestion_key is not None:
            self._sync_dismissal(db, user, record, now)
        verb = "removed" if removed else state
        self._record_activity(db, user, f"kpi.{verb}", record)

    def links(self, user: User, workspace_id: uuid.UUID, kpi_id: uuid.UUID) -> KpiLinks:
        """The DW columns a KPI uses (any member), by Layer, table and position."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = self._load_visible(db, user, workspace_id, kpi_id)
            return KpiLinks(
                record.version, [_link_view(c) for c in described(db, workspace_id, kpi_id)]
            )

    def set_links(
        self,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        *,
        version: int,
        dw_column_ids: Sequence[uuid.UUID],
    ) -> KpiLinks:
        """Replace the DW columns a KPI uses (owners and editors); each is a Core or Mart
        column, at the highest Layer that holds the measure, and becomes a ``kpi`` lineage
        edge. A stale ``version`` is 409 ``version_conflict``; 404 ``not_set_up`` without a
        Data Warehouse; 422 ``invalid_kpi_link``. Changing the links of an approved KPI
        returns it to draft."""
        self._workspaces.authorize(user, Action.EDIT_KPI, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, workspace_id, kpi_id, lock=True)
            if record.version != version:
                raise ApiError(
                    409,
                    "version_conflict",
                    "Someone else changed this KPI since you loaded it. Reload and try again.",
                    {"current_version": record.version},
                )
            if record.review_state not in CATALOG_STATES:
                raise _not_in_catalog(record, "Accept this KPI before linking it to DW columns.")
            columns = check_links(db, workspace_id, dw_column_ids)
            change = self._apply_links(db, user, record, columns)
            return KpiLinks(
                record.version if change else version,
                [_link_view(c) for c in columns],
            )

    def link_candidates(self, user: User, workspace_id: uuid.UUID) -> LinkCandidates:
        """The KPIs of the KPIs tab (live and accepted) that have no links or no formula SQL,
        with the DW Schema to propose them from (any member): the AI's input for a Change
        Set."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        linked = sa.select(KpiLinkRecord.kpi_id)
        query = (
            sa.select(KpiRecord)
            .where(
                KpiRecord.workspace_id == workspace_id,
                KpiRecord.review_state.in_(CATALOG_STATES),
                sa.or_(KpiRecord.formula_sql.is_(None), KpiRecord.id.not_in(linked)),
            )
            .order_by(KpiRecord.name, KpiRecord.id)
            .limit(MAX_CANDIDATES)
        )
        with Session(self._engine) as db:
            return LinkCandidates(
                kpis=[_view(r) for r in db.scalars(query)], schema=read_schema(db, workspace_id)
            )

    def _sync_dismissal(self, db: Session, user: User, record: KpiRecord, now: datetime) -> None:
        """A KPI accepted from a rule suggestion stays dismissed while it is out of the KPIs
        tab (removed), and is not while it is in it (accepted after all)."""
        key = record.suggestion_key
        assert key is not None
        dismissal = db.get(KpiSuggestionDismissalRecord, (record.workspace_id, key))
        if record.review_state in CATALOG_STATES:
            if dismissal is not None:
                db.delete(dismissal)
        elif dismissal is None:
            db.add(
                KpiSuggestionDismissalRecord(
                    workspace_id=record.workspace_id,
                    key=key,
                    dismissed_by=user.id,
                    dismissed_at=now,
                )
            )

    def _rejected_suggestions(
        self, db: Session, workspace_id: uuid.UUID
    ) -> list[RejectedRuleSuggestion]:
        """The dismissed rule-based suggestions still in the DW Schema, by name. One whose
        KPI was removed is the rejected KPI itself, not listed here."""
        schema = read_schema(db, workspace_id)
        if schema is None:
            return []
        dismissals = {
            d.key: d
            for d in db.scalars(
                sa.select(KpiSuggestionDismissalRecord).where(
                    KpiSuggestionDismissalRecord.workspace_id == workspace_id
                )
            )
        }
        kept = self._kpi_keys(db, workspace_id)
        return [
            RejectedRuleSuggestion(
                s, dismissals[s.key].dismissed_by, dismissals[s.key].dismissed_at
            )
            for s in suggest(schema, read_mapping_state(db, workspace_id))
            if s.key in dismissals and s.key not in kept
        ]

    @staticmethod
    def _kpi_keys(db: Session, workspace_id: uuid.UUID) -> set[str]:
        """The keys of the suggestions that became a KPI, in any review state."""
        return set(
            db.scalars(
                sa.select(KpiRecord.suggestion_key).where(
                    KpiRecord.workspace_id == workspace_id,
                    KpiRecord.suggestion_key.is_not(None),
                )
            )
        )

    def _open_suggestions(
        self, db: Session, workspace_id: uuid.UUID, *, include_dismissed: bool = False
    ) -> tuple[list[RuleSuggestion], DwSchema | None]:
        schema = read_schema(db, workspace_id)
        if schema is None:
            return [], None
        hidden = self._kpi_keys(db, workspace_id)
        if not include_dismissed:
            hidden |= set(
                db.scalars(
                    sa.select(KpiSuggestionDismissalRecord.key).where(
                        KpiSuggestionDismissalRecord.workspace_id == workspace_id
                    )
                )
            )
        taken = {
            _name_key(name)
            for name in db.scalars(
                sa.select(KpiRecord.name).where(
                    KpiRecord.workspace_id == workspace_id,
                    KpiRecord.review_state.in_(CATALOG_STATES),
                )
            )
        }
        return [
            s
            for s in suggest(schema, read_mapping_state(db, workspace_id))
            if s.key not in hidden and _name_key(s.name) not in taken
        ], schema

    def _apply_links(
        self, db: Session, user: User, record: KpiRecord, columns: Sequence[SchemaColumn]
    ) -> bool:
        """Store the links on the locked ``record`` and audit the change; ``False`` if there
        was none."""
        old = sorted(str(i) for i in link_ids(db, record.id))
        new = sorted(str(c.id) for c in columns)
        if old == new:
            return False
        write_links(db, record.id, [c.id for c in columns])
        old_fields: dict[str, Any] = {"links": old}
        new_fields: dict[str, Any] = {"links": new}
        if record.status == "approved":
            old_fields["status"], new_fields["status"] = "approved", "draft"
            record.status = "draft"
        record.version += 1
        now = self._clock()
        record.updated_at = now
        db.flush()
        record_audit(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            entity_type="kpi",
            entity_id=record.id,
            old=old_fields,
            new=new_fields,
            at=now,
        )
        self._record_activity(db, user, "kpi.edited", record, details={"fields": ["links"]})
        return True

    def _load_visible(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        kpi_id: uuid.UUID,
        *,
        catalog_only: bool = False,
    ) -> KpiRecord:
        """The KPI, if ``user`` may read it: one in the KPIs tab for any member, a suggested
        or rejected one only with ``kpi.view_review`` and never with ``catalog_only``; else
        404, as if it did not exist."""
        record = self._load(db, workspace_id, kpi_id)
        if record.review_state not in CATALOG_STATES:
            if catalog_only:
                raise _not_found()
            try:
                self._workspaces.authorize(user, Action.VIEW_KPI_REVIEW, workspace_id)
            except ApiError:
                raise _not_found() from None
        return record

    def _load(
        self, db: Session, workspace_id: uuid.UUID, kpi_id: uuid.UUID, *, lock: bool = False
    ) -> KpiRecord:
        query = sa.select(KpiRecord).where(KpiRecord.id == kpi_id)
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        # A KPI under another Workspace's URL is as missing as one that does not exist.
        if record is None or record.workspace_id != workspace_id:
            raise _not_found()
        return record

    def _record_activity(
        self,
        db: Session,
        user: User,
        verb: str,
        record: KpiRecord,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        record_activity(
            db,
            workspace_id=record.workspace_id,
            actor_id=user.id,
            verb=verb,
            object_type="kpi",
            object_id=record.id,
            object_label=record.name,
            details={
                "source_system_id": str(record.source_system_id)
                if record.source_system_id
                else None,
                **(details or {}),
            },
            at=self._clock(),
        )


def _feasibility(
    schema: DwSchema,
    mapped: Collection[uuid.UUID],
    links: set[uuid.UUID],
    formula_sql: str | None,
) -> KpiFeasibility:
    inputs = set(links)
    if formula_sql:
        read = formula_columns(schema, formula_sql)
        if read is None:
            return KpiFeasibility(NEEDS_DATA, [])
        inputs |= read
    if not inputs:
        return KpiFeasibility(NEEDS_DATA, [])
    columns = {c.id: c for t in schema.tables for c in t.columns}
    state = feasibility_of(inputs, columns, mapped)
    unmapped = sorted(
        f"{columns[i].table_name}.{columns[i].name}"
        for i in inputs
        if i in columns and i not in mapped
    )
    return KpiFeasibility(state, unmapped if state != COMPUTABLE else [])


def _views(db: Session, workspace_id: uuid.UUID, records: Sequence[KpiRecord]) -> list[Kpi]:
    """The records as views with their link counts and, for those with links, their
    feasibility: one read of the links, the DW Schema and the mappings for them all."""
    links: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if records:
        for kpi_id, column_id in db.execute(
            sa.select(KpiLinkRecord.kpi_id, KpiLinkRecord.dw_column_id).where(
                KpiLinkRecord.kpi_id.in_([r.id for r in records])
            )
        ):
            links[kpi_id].add(column_id)
    schema = read_schema(db, workspace_id) if links else None
    mapped = read_mapping_state(db, workspace_id).mapped if schema is not None else set()
    return [
        _view(
            r,
            link_count=len(links.get(r.id, ())),
            feasibility=_feasibility(schema, mapped, links[r.id], r.formula_sql).feasibility
            if schema is not None and links.get(r.id)
            else None,
        )
        for r in records
    ]


def _revoke_approval(
    record: KpiRecord, before: Mapping[str, Any], changes: Mapping[str, Any]
) -> None:
    """A structural edit (formula SQL) of an approved KPI returns it to draft, unless the
    same edit sets the status (spec story 80)."""
    if (
        before["status"] == "approved"
        and "status" not in changes
        and record.formula_sql != before["formula_sql"]
    ):
        record.status = "draft"


def _clean_sql(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > SQL_MAX_LENGTH:
        raise _invalid(
            f"The formula SQL must be at most {SQL_MAX_LENGTH} characters.", "formula_sql"
        )
    return cleaned or None
