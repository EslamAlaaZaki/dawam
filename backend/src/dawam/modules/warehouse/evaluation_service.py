"""Advisory AI evaluation of a Layer (spec §6.11, story 122).

The assistant's ``evaluate_dw`` tool reads a Layer's design with ``review`` and stores what it
found with ``store``: findings about things the rules cannot judge (an ambiguous grain, a
dimension that looks like it needs SCD2, a missing conformed dimension, a KPI the model cannot
compute). Any member may run and read one; a finding that names concrete changes becomes a
Change Set through ``propose_change_set`` (editors). Findings never feed the score or the gate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.changesets import ChangeSetDetail, ChangeSetService, ProposedItem
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .model_service import ModelService
from .tables import AiEvaluationRecord, DataWarehouseRecord

LAYERS = ("staging", "core", "mart")
CATEGORIES = (
    "ambiguous_grain",
    "scd2_candidate",
    "missing_conformed_dimension",
    "uncomputable_kpi",
    "other",
)
SEVERITIES = ("high", "medium", "low")
MAX_FINDINGS = 50
MAX_REVIEW_TABLES = 200
MAX_ITEMS_PER_FINDING = 50
TEXT_MAX_LENGTH = 2000


@dataclass(frozen=True)
class FindingItem:
    """One concrete change a finding proposes (what a Change Set item needs)."""

    object_type: str
    operation: str
    payload: dict[str, Any]
    object_id: uuid.UUID | None = None
    label: str = ""


@dataclass(frozen=True)
class Finding:
    title: str
    detail: str
    category: str = "other"
    severity: str = "medium"
    table_id: uuid.UUID | None = None
    suggestion: str = ""
    items: list[FindingItem] = field(default_factory=list)
    """Concrete changes, when the finding has them; empty: advice only."""


@dataclass(frozen=True)
class AiEvaluation:
    id: uuid.UUID
    layer: str
    findings: list[dict[str, Any]]
    created_by: uuid.UUID | None
    created_at: datetime


def _not_set_up() -> ApiError:
    return ApiError(404, "not_found", "The Data Warehouse is not set up yet.")


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_evaluation", message)


def _view(record: AiEvaluationRecord) -> AiEvaluation:
    return AiEvaluation(
        record.id, record.layer, list(record.findings), record.created_by, record.created_at
    )


def _check_layer(layer: str) -> None:
    if layer not in LAYERS:
        raise _invalid(f"`layer` is one of {', '.join(LAYERS)}.")


def _finding_json(finding: Finding) -> dict[str, Any]:
    if not finding.title.strip() or not finding.detail.strip():
        raise _invalid("A finding needs a title and a detail.")
    if finding.category not in CATEGORIES:
        raise _invalid(f"A finding's category is one of {', '.join(CATEGORIES)}.")
    if finding.severity not in SEVERITIES:
        raise _invalid(f"A finding's severity is one of {', '.join(SEVERITIES)}.")
    if len(finding.items) > MAX_ITEMS_PER_FINDING:
        raise _invalid(f"A finding proposes at most {MAX_ITEMS_PER_FINDING} changes.")
    return {
        "title": finding.title.strip()[:200],
        "detail": finding.detail.strip()[:TEXT_MAX_LENGTH],
        "category": finding.category,
        "severity": finding.severity,
        "table_id": str(finding.table_id) if finding.table_id else None,
        "suggestion": finding.suggestion.strip()[:TEXT_MAX_LENGTH],
        "items": [
            {
                "object_type": i.object_type,
                "operation": i.operation,
                "object_id": str(i.object_id) if i.object_id else None,
                "payload": i.payload,
                "label": i.label[:200],
            }
            for i in finding.items
        ],
    }


class EvaluationService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        model: ModelService,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._model = model
        self._clock = clock

    def review(self, user: User, workspace_id: uuid.UUID, layer: str) -> dict[str, Any]:
        """What the evaluator reads: the Layer's tables with grain, SCD type, conformance and
        columns, plus the tables the other Layers have (so a missing conformed dimension can
        be told). Any member who may ask the assistant; 404 before set up."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        _check_layer(layer)
        summaries = self._model.list_tables(user, workspace_id)
        with Session(self._engine) as db:
            if self._warehouse_id(db, workspace_id) is None:
                raise _not_set_up()
        in_layer = [s for s in summaries if s.layer == layer]
        names = {s.id: f"{s.layer}.{s.name}" for s in summaries}
        tables = []
        for summary in in_layer[:MAX_REVIEW_TABLES]:
            table = self._model.get_table(user, workspace_id, summary.id)
            tables.append(
                {
                    "id": table.id,
                    "name": table.name,
                    "kind": table.kind,
                    "fact_type": table.fact_type,
                    "grain": table.grain,
                    "scd_type": table.scd_type,
                    "is_conformed": table.is_conformed,
                    "description": table.description,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.data_type,
                            "role": c.role,
                            "references": names.get(c.references_table_id)
                            if c.references_table_id
                            else None,
                        }
                        for c in table.columns
                        if not c.is_system
                    ],
                }
            )
        return {
            "layer": layer,
            "tables": tables,
            "truncated": len(in_layer) > MAX_REVIEW_TABLES,
            "other_tables": [
                f"{s.layer}.{s.name} ({s.kind})" for s in summaries if s.layer != layer
            ],
        }

    def store(
        self, user: User, workspace_id: uuid.UUID, layer: str, findings: list[Finding]
    ) -> AiEvaluation:
        """Save the evaluation of ``layer`` (any member who may ask the assistant; refused in
        an archived Workspace). An empty list is a clean bill and is stored too. 422
        ``invalid_evaluation``."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        _check_layer(layer)
        if len(findings) > MAX_FINDINGS:
            raise _invalid(f"An evaluation has at most {MAX_FINDINGS} findings.")
        stored = [_finding_json(f) for f in findings]
        with Session(self._engine) as db, db.begin():
            warehouse_id = self._warehouse_id(db, workspace_id)
            if warehouse_id is None:
                raise _not_set_up()
            record = AiEvaluationRecord(
                id=uuid.uuid4(),
                data_warehouse_id=warehouse_id,
                layer=layer,
                findings=stored,
                created_by=user.id,
                created_at=self._clock(),
            )
            db.add(record)
            db.flush()
            return _view(record)

    def list(
        self, user: User, workspace_id: uuid.UUID, *, layer: str | None = None, limit: int = 10
    ) -> list[AiEvaluation]:
        """Stored evaluations, newest first (any member)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        if layer is not None:
            _check_layer(layer)
        with Session(self._engine) as db:
            warehouse_id = self._warehouse_id(db, workspace_id)
            if warehouse_id is None:
                raise _not_set_up()
            query = (
                sa.select(AiEvaluationRecord)
                .where(AiEvaluationRecord.data_warehouse_id == warehouse_id)
                .order_by(AiEvaluationRecord.created_at.desc(), AiEvaluationRecord.id.desc())
                .limit(limit)
            )
            if layer is not None:
                query = query.where(AiEvaluationRecord.layer == layer)
            return [_view(r) for r in db.scalars(query)]

    def propose_change_set(
        self,
        user: User,
        workspace_id: uuid.UUID,
        evaluation_id: uuid.UUID,
        index: int,
        *,
        change_sets: ChangeSetService,
    ) -> ChangeSetDetail:
        """Turn a finding's concrete changes into a pending Change Set (owners and editors;
        a viewer is refused). 404 for an unknown evaluation or finding; 422
        ``no_changes`` for a finding that is advice only."""
        self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        with Session(self._engine) as db:
            record = db.scalars(
                sa.select(AiEvaluationRecord)
                .join(
                    DataWarehouseRecord,
                    DataWarehouseRecord.id == AiEvaluationRecord.data_warehouse_id,
                )
                .where(
                    AiEvaluationRecord.id == evaluation_id,
                    DataWarehouseRecord.workspace_id == workspace_id,
                )
            ).first()
            if record is None or not 0 <= index < len(record.findings):
                raise ApiError(404, "not_found", "That finding does not exist.")
            finding = record.findings[index]
        if not finding["items"]:
            raise ApiError(
                422,
                "no_changes",
                "This finding is advice only; it names no concrete change to propose.",
            )
        items = [
            ProposedItem(
                key=f"f{n}",
                object_type=i["object_type"],
                operation=i["operation"],
                payload=i["payload"],
                object_id=uuid.UUID(i["object_id"]) if i["object_id"] else None,
                label=i["label"],
            )
            for n, i in enumerate(finding["items"])
        ]
        return change_sets.propose(
            user,
            workspace_id,
            origin="ai",
            scope={"evaluation_id": str(evaluation_id), "finding": index},
            title=f"AI evaluation: {finding['title']}",
            items=items,
        )

    @staticmethod
    def _warehouse_id(db: Session, workspace_id: uuid.UUID) -> uuid.UUID | None:
        return db.scalars(
            sa.select(DataWarehouseRecord.id).where(
                DataWarehouseRecord.workspace_id == workspace_id
            )
        ).first()
