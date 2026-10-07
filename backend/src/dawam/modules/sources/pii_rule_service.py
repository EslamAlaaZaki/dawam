"""The Workspace's PII rules (spec §6.12, story 136).

Owners add custom rules (name keywords and/or a regex for sampled values, a category and a
confidence) so organisation-specific identifiers such as employee numbers are caught, and
switch a built-in rule off for the Workspace. A built-in rule is never edited. The rules
apply to the name scan of every new Snapshot and to value scans (``internal.pii.load_rule_set``
is the one place that reads them); existing findings are left as they are. Every change is
audited (``via=user``, entity ``pii_rule``) and recorded in the activity feed, in the
transaction that makes it. Rules never hold a data value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dawam.modules.activity import record_activity
from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import VALIDATORS

from .internal.pii_rules import RULES, compile_custom_rule
from .tables import DESCRIPTION_MAX_LENGTH, PiiCustomRuleRecord, PiiDisabledRuleRecord

_UNSET: Any = object()
"""Marks a field an update leaves alone (``None`` is a real value for ``pattern``)."""

_EDITABLE = ("description", "keywords", "pattern", "category", "confidence")


@dataclass(frozen=True)
class CustomPiiRule:
    id: uuid.UUID
    name: str
    description: str
    keywords: list[str]
    pattern: str | None
    category: str
    confidence: float
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BuiltInPiiRule:
    id: str
    category: str
    confidence: float
    enabled: bool


@dataclass(frozen=True)
class PiiRules:
    built_in: list[BuiltInPiiRule]
    custom: list[CustomPiiRule]


def _not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")


def _view(record: PiiCustomRuleRecord) -> CustomPiiRule:
    return CustomPiiRule(
        id=record.id,
        name=record.name,
        description=record.description,
        keywords=list(record.keywords),
        pattern=record.pattern,
        category=record.category,
        confidence=record.confidence,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _snapshot(record: PiiCustomRuleRecord) -> dict[str, Any]:
    """What an audit entry says about a custom rule."""
    return {
        "name": record.name,
        "description": record.description,
        "keywords": list(record.keywords),
        "pattern": record.pattern,
        "category": record.category,
        "confidence": record.confidence,
    }


def _built_in_ids() -> list[str]:
    return sorted(set(RULES) | set(VALIDATORS))


def _built_in_view(rule_id: str, enabled: bool) -> BuiltInPiiRule:
    rule = RULES.get(rule_id)
    validator = VALIDATORS.get(rule_id)
    category = rule.category if rule is not None else validator.category  # type: ignore[union-attr]
    confidence = rule.confidence if rule is not None else validator.weight  # type: ignore[union-attr]
    return BuiltInPiiRule(id=rule_id, category=category, confidence=confidence, enabled=enabled)


class PiiRuleService:
    """Every method authorizes through the workspaces policy first (owners only)."""

    def __init__(self, engine: sa.Engine, *, workspaces: WorkspaceService, clock: Clock) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock

    def list_rules(self, user: User, workspace_id: uuid.UUID) -> PiiRules:
        """Every built-in rule with its on/off state, and the custom rules by name."""
        self._workspaces.authorize(user, Action.MANAGE_PII_RULES, workspace_id)
        with Session(self._engine) as db:
            disabled = set(
                db.scalars(
                    sa.select(PiiDisabledRuleRecord.rule).where(
                        PiiDisabledRuleRecord.workspace_id == workspace_id
                    )
                )
            )
            custom = db.scalars(
                sa.select(PiiCustomRuleRecord)
                .where(PiiCustomRuleRecord.workspace_id == workspace_id)
                .order_by(PiiCustomRuleRecord.name)
            )
            return PiiRules(
                built_in=[_built_in_view(r, r not in disabled) for r in _built_in_ids()],
                custom=[_view(r) for r in custom],
            )

    def create(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        name: str,
        keywords: list[str],
        pattern: str | None,
        category: str,
        confidence: float,
        description: str = "",
    ) -> CustomPiiRule:
        """Add a custom rule. 422 ``invalid_pii_rule`` if it is malformed, 409 if the
        Workspace already has a rule of that name."""
        self._workspaces.authorize(user, Action.MANAGE_PII_RULES, workspace_id)
        self._compile(
            name, keywords=keywords, pattern=pattern, category=category, confidence=confidence
        )
        self._check_description(description)
        now = self._clock()
        try:
            return self._insert(
                user, workspace_id, name, description, keywords, pattern, category, confidence, now
            )
        except IntegrityError:  # a concurrent create of the same name
            raise self._exists(name) from None

    def _exists(self, name: str) -> ApiError:
        return ApiError(
            409, "pii_rule_exists", f'The Workspace already has a PII rule named "{name}".'
        )

    def _insert(
        self,
        user: User,
        workspace_id: uuid.UUID,
        name: str,
        description: str,
        keywords: list[str],
        pattern: str | None,
        category: str,
        confidence: float,
        now: datetime,
    ) -> CustomPiiRule:
        with Session(self._engine) as db, db.begin():
            taken = db.scalar(
                sa.select(PiiCustomRuleRecord.id).where(
                    PiiCustomRuleRecord.workspace_id == workspace_id,
                    PiiCustomRuleRecord.name == name,
                )
            )
            if taken is not None:
                raise self._exists(name)
            record = PiiCustomRuleRecord(
                id=uuid.uuid4(),
                workspace_id=workspace_id,
                name=name,
                description=description,
                keywords=keywords,
                pattern=pattern or None,
                category=category,
                confidence=confidence,
                created_by=user.id,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            self._record(db, user, workspace_id, record, "created", None, _snapshot(record), now)
            db.flush()
            return _view(record)

    def update(
        self,
        user: User,
        workspace_id: uuid.UUID,
        rule_id: uuid.UUID,
        *,
        description: str = _UNSET,
        keywords: list[str] = _UNSET,
        pattern: str | None = _UNSET,
        category: str = _UNSET,
        confidence: float = _UNSET,
    ) -> CustomPiiRule:
        """Change a custom rule (a field left out stays). It applies to later scans only."""
        self._workspaces.authorize(user, Action.MANAGE_PII_RULES, workspace_id)
        changes = {
            field: value
            for field, value in zip(
                _EDITABLE, (description, keywords, pattern, category, confidence), strict=True
            )
            if value is not _UNSET
        }
        with Session(self._engine) as db, db.begin():
            record = self._custom(db, workspace_id, rule_id, lock=True)
            old = _snapshot(record)
            merged = {**old, **changes}
            if merged.get("pattern") == "":
                merged["pattern"] = None
            self._compile(
                record.name,
                keywords=merged["keywords"],
                pattern=merged["pattern"],
                category=merged["category"],
                confidence=merged["confidence"],
            )
            self._check_description(merged["description"])
            changed = [f for f in _EDITABLE if merged[f] != old[f]]
            if changed:
                now = self._clock()
                for field in changed:
                    setattr(record, field, merged[field])
                record.updated_at = now
                self._record(
                    db,
                    user,
                    workspace_id,
                    record,
                    "updated",
                    {f: old[f] for f in changed},
                    {f: merged[f] for f in changed},
                    now,
                )
            db.flush()
            return _view(record)

    def delete(self, user: User, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        """Delete a custom rule. Findings it already made stay in the review queue."""
        self._workspaces.authorize(user, Action.MANAGE_PII_RULES, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._custom(db, workspace_id, rule_id, lock=True)
            now = self._clock()
            self._record(db, user, workspace_id, record, "deleted", _snapshot(record), None, now)
            db.delete(record)

    def set_built_in_enabled(
        self, user: User, workspace_id: uuid.UUID, rule_id: str, enabled: bool
    ) -> BuiltInPiiRule:
        """Switch a built-in rule on or off for this Workspace (it is never edited)."""
        self._workspaces.authorize(user, Action.MANAGE_PII_RULES, workspace_id)
        if rule_id not in _built_in_ids():
            raise _not_found("Built-in PII rule")
        with Session(self._engine) as db, db.begin():
            row = db.get(PiiDisabledRuleRecord, (workspace_id, rule_id), with_for_update=True)
            if enabled and row is not None:
                db.delete(row)
            elif not enabled and row is None:
                now = self._clock()
                db.add(
                    PiiDisabledRuleRecord(
                        workspace_id=workspace_id,
                        rule=rule_id,
                        disabled_by=user.id,
                        disabled_at=now,
                    )
                )
            else:
                return _built_in_view(rule_id, enabled)
            now = self._clock()
            old, new = {"enabled": not enabled}, {"enabled": enabled}
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type="pii_rule",
                entity_id=rule_id,
                old=old,
                new=new,
                at=now,
            )
            record_activity(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                verb="pii_rule.enabled" if enabled else "pii_rule.disabled",
                object_type="pii_rule",
                object_id=rule_id,
                object_label=rule_id,
                details={"built_in": True},
                at=now,
            )
            return _built_in_view(rule_id, enabled)

    # -- internals ----------------------------------------------------------------------

    def _custom(
        self, db: Session, workspace_id: uuid.UUID, rule_id: uuid.UUID, *, lock: bool = False
    ) -> PiiCustomRuleRecord:
        record = db.get(PiiCustomRuleRecord, rule_id, with_for_update=lock)
        if record is None or record.workspace_id != workspace_id:
            raise _not_found("PII rule")
        return record

    def _compile(self, name: str, **fields: Any) -> None:
        try:
            compile_custom_rule(name, **fields)
        except ValueError as exc:
            raise ApiError(422, "invalid_pii_rule", str(exc)) from None

    def _check_description(self, description: str) -> None:
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise ApiError(
                422,
                "invalid_pii_rule",
                f"The description is at most {DESCRIPTION_MAX_LENGTH} characters.",
            )

    def _record(
        self,
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        record: PiiCustomRuleRecord,
        verb: str,
        old: dict[str, Any] | None,
        new: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        record_audit(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            entity_type="pii_rule",
            entity_id=record.id,
            old=old,
            new=new,
            at=now,
        )
        record_activity(
            db,
            workspace_id=workspace_id,
            actor_id=user.id,
            verb=f"pii_rule.{verb}",
            object_type="pii_rule",
            object_id=record.id,
            object_label=record.name,
            details={},
            at=now,
        )
