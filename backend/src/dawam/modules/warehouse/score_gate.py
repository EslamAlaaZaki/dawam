"""Disabled checks and the approval gate (spec §6.11, stories 119, 120).

An owner switches a score check off for the Data Warehouse with a reason (it leaves the
formula and is listed apart) and sets, per Core and Mart Layer, a minimum score.
``verify_approval`` is what ``ApprovalService`` asks before it moves
a Layer to ``approved``: it rescores first, so the gate never reads a stale run. Every change
is audited and rescores. Errors block approval whether or not a minimum is set; Staging has no gate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dawam.modules.audit import record_audit
from dawam.modules.auth import User
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .score_service import DisabledCheck, ScoreService
from .tables import (
    APPROVAL_LAYERS,
    DataWarehouseRecord,
    DisabledCheckRecord,
    ScoreGateRecord,
)

REASON_MAX_LENGTH = 500
CHECK_ENTITY = "score_check"
GATE_ENTITY = "score_gate"
"""Audit entity types; the entity ids are the check code and the Layer."""


@dataclass(frozen=True)
class ScoreGate:
    layer: str
    min_score: float
    configured: bool
    """False: no minimum score is set; errors block approval all the same."""
    updated_by: uuid.UUID | None
    updated_at: datetime | None


class ScoreGateService:
    """Reading and changing the disabled checks and gates. Every method authorizes."""

    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        clock: Clock,
        scores: ScoreService,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._clock = clock
        self._scores = scores

    # --- disabled checks ---------------------------------------------------------------

    def disabled(self, user: User, workspace_id: uuid.UUID) -> list[DisabledCheck]:
        """The disabled checks with their reasons (any member). 404 ``not_set_up``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return self._scores.disabled_checks(db, _warehouse_id(db, workspace_id))

    def disable(
        self, user: User, workspace_id: uuid.UUID, check_code: str, reason: str
    ) -> list[DisabledCheck]:
        """Switch a check off with a reason (owners only), or change the reason of one
        already off, and rescore. 404 ``unknown_check``; 422 ``reason_required``."""
        self._workspaces.authorize(user, Action.MANAGE_SCORE_SETTINGS, workspace_id)
        if self._scores.registry.spec(check_code) is None:
            raise ApiError(404, "unknown_check", f"There is no score check {check_code!r}.")
        reason = reason.strip()
        if not reason or len(reason) > REASON_MAX_LENGTH:
            raise ApiError(
                422,
                "reason_required",
                f"Say why the check is disabled, in 1 to {REASON_MAX_LENGTH} characters.",
            )
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            warehouse_id = _warehouse_id(db, workspace_id, lock=True)
            previous = db.get(DisabledCheckRecord, (warehouse_id, check_code))
            old = {"disabled": False} if previous is None else _disabled(previous.reason)
            db.execute(
                pg_insert(DisabledCheckRecord)
                .values(
                    data_warehouse_id=warehouse_id,
                    check_code=check_code,
                    reason=reason,
                    disabled_by=user.id,
                    disabled_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["data_warehouse_id", "check_code"],
                    set_={"reason": reason, "disabled_by": user.id, "disabled_at": now},
                )
            )
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type=CHECK_ENTITY,
                entity_id=check_code,
                old=old,
                new=_disabled(reason),
                at=now,
            )
        self._scores.recalculate(warehouse_id)
        return self.disabled(user, workspace_id)

    def enable(self, user: User, workspace_id: uuid.UUID, check_code: str) -> list[DisabledCheck]:
        """Switch a disabled check back on (owners only) and rescore. 404 ``not_disabled``."""
        self._workspaces.authorize(user, Action.MANAGE_SCORE_SETTINGS, workspace_id)
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            warehouse_id = _warehouse_id(db, workspace_id, lock=True)
            row = db.get(DisabledCheckRecord, (warehouse_id, check_code))
            if row is None:
                raise ApiError(404, "not_disabled", f"The check {check_code!r} is not disabled.")
            old = _disabled(row.reason)
            db.delete(row)
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type=CHECK_ENTITY,
                entity_id=check_code,
                old=old,
                new={"disabled": False},
                at=now,
            )
        self._scores.recalculate(warehouse_id)
        return self.disabled(user, workspace_id)

    # --- gates -------------------------------------------------------------------------

    def gates(self, user: User, workspace_id: uuid.UUID) -> list[ScoreGate]:
        """Core's and Mart's gate (any member). 404 ``not_set_up``."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            return _gates(db, _warehouse_id(db, workspace_id))

    def set_gate(
        self,
        user: User,
        workspace_id: uuid.UUID,
        layer: str,
        min_score: float,
    ) -> ScoreGate:
        """Set the minimum score (0-100) of the Core or Mart Layer and whether errors block
        approval (owners only). 422 ``no_layer_approval`` for staging."""
        self._workspaces.authorize(user, Action.MANAGE_SCORE_SETTINGS, workspace_id)
        if layer not in APPROVAL_LAYERS:
            raise ApiError(422, "no_layer_approval", "Only Core and Mart have an approval gate.")
        if not 0 <= min_score <= 100:
            raise ApiError(422, "validation_error", "The minimum score is from 0 to 100.")
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            warehouse_id = _warehouse_id(db, workspace_id, lock=True)
            previous = db.get(ScoreGateRecord, (warehouse_id, layer))
            old = None if previous is None else {"min_score": previous.min_score}
            values = {"min_score": min_score}
            db.execute(
                pg_insert(ScoreGateRecord)
                .values(
                    data_warehouse_id=warehouse_id,
                    layer=layer,
                    updated_by=user.id,
                    updated_at=now,
                    **values,
                )
                .on_conflict_do_update(
                    index_elements=["data_warehouse_id", "layer"],
                    set_={**values, "updated_by": user.id, "updated_at": now},
                )
            )
            record_audit(
                db,
                workspace_id=workspace_id,
                actor_id=user.id,
                entity_type=GATE_ENTITY,
                entity_id=layer,
                old=old,
                new=values,
                at=now,
            )
        return next(g for g in self.gates(user, workspace_id) if g.layer == layer)

    def verify_approval(self, workspace_id: uuid.UUID, layer: str) -> None:
        """Raise 409 ``score_gate_failed`` when the Layer may not be approved: it has
        unresolved errors, or it scores below the minimum when an owner set one. Rescores
        first, so a disabled check no longer counts. A Layer with no tables is not scored and
        has no errors, so it passes."""
        with Session(self._engine) as db:
            warehouse_id = _warehouse_id(db, workspace_id)
            gate = db.get(ScoreGateRecord, (warehouse_id, layer))
        report = self._scores.recalculate(warehouse_id)
        if report is None:
            return
        errors = sum(
            1
            for f in report.findings
            if f.layer == layer and not f.passed and f.severity == "error"
        )
        item = next((x for x in report.layers if x.layer == layer), None)
        score = item.score if item else None
        reasons = []
        if errors:
            reasons.append(f"it has {errors} unresolved error{'s' if errors != 1 else ''}")
        if gate is not None and score is not None and score < gate.min_score:
            reasons.append(f"its score {score:g} is below the minimum {gate.min_score:g}")
        if reasons:
            raise ApiError(
                409,
                "score_gate_failed",
                f"The {layer.capitalize()} Layer cannot be approved: {' and '.join(reasons)}.",
                {
                    "layer": layer,
                    "score": score,
                    "min_score": gate.min_score if gate else None,
                    "error_count": errors,
                },
            )


def _disabled(reason: str) -> dict[str, object]:
    return {"disabled": True, "reason": reason}


def _warehouse_id(db: Session, workspace_id: uuid.UUID, *, lock: bool = False) -> uuid.UUID:
    query = sa.select(DataWarehouseRecord.id).where(
        DataWarehouseRecord.workspace_id == workspace_id
    )
    if lock:
        query = query.with_for_update()
    found = db.scalars(query).first()
    if found is None:
        raise ApiError(404, "not_set_up", "The Data Warehouse has not been set up yet.")
    return found


def _gates(db: Session, warehouse_id: uuid.UUID) -> list[ScoreGate]:
    rows = {
        r.layer: r
        for r in db.scalars(
            sa.select(ScoreGateRecord).where(ScoreGateRecord.data_warehouse_id == warehouse_id)
        )
    }
    return [
        ScoreGate(layer, r.min_score, True, r.updated_by, r.updated_at)
        if (r := rows.get(layer))
        else ScoreGate(layer, 0, False, None, None)
        for layer in APPROVAL_LAYERS
    ]
