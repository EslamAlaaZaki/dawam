"""The admin run explorer: every Workspace's assistant runs, step by step (admins only).

A run stores its tool calls, token totals, duration, error and prompt version; the question and
answer are its two messages. Nothing here records more than the run did: the timeline is the
stored steps, the prompt and answer are redacted when shown, and the cost is the run's tokens at
the answering model's price (unknown, not zero, when the model has none).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, aliased

from dawam.modules.auth import User
from dawam.modules.llm import ModelPrice, RoleService, WorkspaceAiService, call_cost
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.errors import ApiError
from dawam.platform.pii_validators import redact_json, redact_text

from .tables import ConversationRecord, MessageRecord, RunFeedbackRecord, RunRecord

RUN_LIST_LIMIT = 100
RUN_STATUSES = ("running", "completed", "tool_limit", "cancelled", "failed")


@dataclass(frozen=True)
class RunCost:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class RunSummary:
    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str | None
    conversation_id: uuid.UUID
    member_id: uuid.UUID | None
    member_email: str | None
    status: str
    prompt_version: str | None
    model_name: str | None
    input_tokens: int
    output_tokens: int
    duration_ms: int
    cost: RunCost | None
    tool_call_count: int
    thumbs_up: int
    thumbs_down: int
    started_at: datetime


@dataclass(frozen=True)
class RunStep:
    """``kind`` is ``model`` (the run's model calls, summed), ``tool`` or ``error``."""

    kind: str
    name: str
    status: str
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: RunCost | None = None
    prompt: str | None = None
    output: str | None = None
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RunFeedbackEntry:
    member_email: str | None
    rating: str
    comment: str | None
    updated_at: datetime


@dataclass(frozen=True)
class RunDetail:
    summary: RunSummary
    finished_at: datetime | None
    timeline: list[RunStep]
    feedback: list[RunFeedbackEntry] = field(default_factory=list)


class RunExplorer:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        roles: RoleService,
        ai: WorkspaceAiService,
        users: Callable[[uuid.UUID], User | None],
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._roles = roles
        self._ai = ai
        self._users = users

    def list_runs(
        self,
        user: User,
        *,
        workspace_id: uuid.UUID | None = None,
        member_id: uuid.UUID | None = None,
        status: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
    ) -> list[RunSummary]:
        """Newest first, at most ``RUN_LIST_LIMIT``; 403 unless an admin."""
        _require_admin(user)
        if status is not None and status not in RUN_STATUSES:
            raise ApiError(422, "invalid_status", "That is not a run status.")
        question = aliased(MessageRecord)
        query = (
            sa.select(RunRecord, ConversationRecord.workspace_id, question.sender_user_id)
            .join(ConversationRecord, ConversationRecord.id == RunRecord.conversation_id)
            .join(question, question.id == RunRecord.user_message_id)
            .order_by(RunRecord.started_at.desc(), RunRecord.id)
            .limit(RUN_LIST_LIMIT)
        )
        if workspace_id is not None:
            query = query.where(ConversationRecord.workspace_id == workspace_id)
        if member_id is not None:
            query = query.where(question.sender_user_id == member_id)
        if status is not None:
            query = query.where(RunRecord.status == status)
        if started_from is not None:
            query = query.where(RunRecord.started_at >= started_from)
        if started_to is not None:
            query = query.where(RunRecord.started_at < started_to)
        with Session(self._engine) as db:
            rows = [tuple(r) for r in db.execute(query).all()]
            ids = [run.id for run, *_ in rows]
            votes = _votes(db, ids)
            answers = _answer_models(db, ids)
        return self._summaries(rows, votes, answers)

    def get_run(self, user: User, run_id: uuid.UUID) -> RunDetail:
        """The run with its timeline and every member's feedback; 404 if unknown."""
        _require_admin(user)
        question = aliased(MessageRecord)
        answer = aliased(MessageRecord)
        with Session(self._engine) as db:
            row = db.execute(
                sa.select(
                    RunRecord,
                    ConversationRecord.workspace_id,
                    question.sender_user_id,
                    question.content,
                    answer.content,
                )
                .join(ConversationRecord, ConversationRecord.id == RunRecord.conversation_id)
                .join(question, question.id == RunRecord.user_message_id)
                .outerjoin(answer, answer.id == RunRecord.answer_message_id)
                .where(RunRecord.id == run_id)
            ).first()
            if row is None:
                raise ApiError(404, "not_found", "That run does not exist.")
            run, workspace_id, sender, prompt, answer_text = row
            votes = _votes(db, [run.id])
            answers = _answer_models(db, [run.id])
            ratings = list(
                db.scalars(
                    sa.select(RunFeedbackRecord)
                    .where(RunFeedbackRecord.run_id == run_id)
                    .order_by(RunFeedbackRecord.updated_at, RunFeedbackRecord.id)
                )
            )
        summary = self._summaries([(run, workspace_id, sender)], votes, answers)[0]
        timeline = [
            RunStep(
                "model",
                summary.model_name or "model",
                "failed" if run.status == "failed" else "completed",
                duration_ms=run.duration_ms,
                input_tokens=run.input_tokens,
                output_tokens=run.output_tokens,
                cost=summary.cost,
                prompt=redact_text(prompt),
                output=redact_text(answer_text) if answer_text else None,
            )
        ]
        timeline += [
            RunStep(
                "tool",
                str(c["name"]),
                str(c["status"]),
                duration_ms=c.get("duration_ms"),
                arguments=redact_json(c.get("arguments") or {}),
                result=redact_json(c["result"]) if c.get("result") else None,
            )
            for c in run.tool_calls
        ]
        if run.error_code or run.error_message:
            timeline.append(
                RunStep(
                    "error",
                    run.error_code or "error",
                    "failed",
                    error_code=run.error_code,
                    error_message=redact_text(run.error_message or ""),
                )
            )
        members = {r.user_id: self._users(r.user_id) for r in ratings}
        feedback = []
        for r in ratings:
            member = members[r.user_id]
            comment = redact_text(r.comment) if r.comment else None
            feedback.append(
                RunFeedbackEntry(member.email if member else None, r.rating, comment, r.updated_at)
            )
        return RunDetail(summary, run.finished_at, timeline, feedback)

    def _summaries(
        self,
        rows: list[Any],
        votes: dict[uuid.UUID, tuple[int, int]],
        answers: dict[uuid.UUID, uuid.UUID],
    ) -> list[RunSummary]:
        model_names = self._ai.model_names(set(answers.values()))
        prices = {p.model_name: p for p in self._roles.prices()}
        workspace_names = self._workspaces.names_by_id({w for _, w, _ in rows})
        members = {s: self._users(s) for s in {s for *_, s in rows if s}}
        out = []
        for run, workspace_id, sender in rows:
            model_id = answers.get(run.id)
            model_name = model_names.get(model_id) if model_id else None
            up, down = votes.get(run.id, (0, 0))
            member = members.get(sender) if sender else None
            out.append(
                RunSummary(
                    id=run.id,
                    workspace_id=workspace_id,
                    workspace_name=workspace_names.get(workspace_id),
                    conversation_id=run.conversation_id,
                    member_id=sender,
                    member_email=member.email if member else None,
                    status=run.status,
                    prompt_version=run.prompt_version,
                    model_name=model_name,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    duration_ms=run.duration_ms,
                    cost=_cost(prices.get(model_name or ""), run),
                    tool_call_count=len(run.tool_calls),
                    thumbs_up=up,
                    thumbs_down=down,
                    started_at=run.started_at,
                )
            )
        return out


def _require_admin(user: User) -> None:
    if not can(user, Action.VIEW_ASSISTANT_RUNS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")


def _cost(price: ModelPrice | None, run: RunRecord) -> RunCost | None:
    if price is None:
        return None
    return RunCost(call_cost(price, run.input_tokens, run.output_tokens, 0), price.currency)


def _votes(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    up = sa.func.count().filter(RunFeedbackRecord.rating == "up")
    down = sa.func.count().filter(RunFeedbackRecord.rating == "down")
    rows = db.execute(
        sa.select(RunFeedbackRecord.run_id, up, down)
        .where(RunFeedbackRecord.run_id.in_(run_ids))
        .group_by(RunFeedbackRecord.run_id)
    )
    return {run_id: (u, d) for run_id, u, d in rows}


def _answer_models(db: Session, run_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    rows = db.execute(
        sa.select(RunRecord.id, MessageRecord.model_id)
        .join(MessageRecord, MessageRecord.id == RunRecord.answer_message_id)
        .where(RunRecord.id.in_(run_ids), MessageRecord.model_id.is_not(None))
    )
    return {run_id: model_id for run_id, model_id in rows}
