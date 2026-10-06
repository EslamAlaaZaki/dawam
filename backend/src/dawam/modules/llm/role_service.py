"""Model roles, monthly token budgets and AI usage (spec §6.18, stories 157, 159, 162).

Authorization is the caller's job (the router restricts the admin endpoints to admins);
the service trusts its caller. Every model call made for a feature goes through
``gateway_for_role``: the returned ``MeteredGateway`` checks the installation's and the
Workspace's monthly budgets before each call and records the tokens the call used.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .gateway import (
    Capabilities,
    ChatEvent,
    Done,
    Gateway,
    Message,
    TextDelta,
    ToolCallEvent,
    ToolSpec,
    Usage,
    _message_text,
    estimate_tokens,
)
from .internal import reindex
from .service import ProviderService
from .tables import (
    MODEL_NAME_MAX_LENGTH,
    ModelRecord,
    SettingsRecord,
    UsageRecord,
    WorkspaceBudgetRecord,
)

MAX_BUDGET = 10**15


@dataclass(frozen=True)
class RoleAssignments:
    agent_model_id: uuid.UUID | None
    light_model_id: uuid.UUID | None
    embedding_model_id: uuid.UUID | None
    reindex_needed: bool
    """True after the embedding model or its vector dimension changed: documents must be
    indexed again (the re-index job comes with document search)."""
    reindex_reason: str | None
    reindex_flagged_at: datetime | None


@dataclass(frozen=True)
class WorkspaceBudget:
    workspace_id: uuid.UUID
    monthly_token_budget: int


@dataclass(frozen=True)
class Budgets:
    installation: int | None
    workspaces: list[WorkspaceBudget]


@dataclass(frozen=True)
class UsageTotals:
    prompt_tokens: int
    completion_tokens: int
    calls: int
    estimated_calls: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class WorkspaceUsage:
    workspace_id: uuid.UUID | None
    """Null: calls made for no Workspace, or for one since deleted."""
    totals: UsageTotals
    by_role: dict[str, int]
    """Total tokens per model role."""


@dataclass(frozen=True)
class UserUsage:
    user_id: uuid.UUID | None
    totals: UsageTotals
    by_role: dict[str, int]


@dataclass(frozen=True)
class UsageReport:
    month: str
    """``YYYY-MM`` (UTC)."""
    totals: UsageTotals
    installation_budget: int | None
    workspaces: list[WorkspaceUsage]
    users: list[UserUsage]


def parse_month(value: str) -> tuple[datetime, datetime]:
    """The ``[start, end)`` of a ``YYYY-MM`` month in UTC."""
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        raise ApiError(
            422, "invalid_month", "The month must look like 2026-10.", {"field": "month"}
        ) from None
    start = datetime(parsed.year, parsed.month, 1, tzinfo=UTC)
    end = (
        datetime(parsed.year + 1, 1, 1, tzinfo=UTC)
        if parsed.month == 12
        else datetime(parsed.year, parsed.month + 1, 1, tzinfo=UTC)
    )
    return start, end


def _budget_exhausted(scope: str, budget: int, used: int) -> ApiError:
    what = "installation's" if scope == "installation" else "Workspace's"
    return ApiError(
        429,
        "token_budget_exhausted",
        f"The {what} monthly AI token budget is used up. Everything else keeps working; "
        "ask an admin to raise the budget or wait for next month.",
        {"scope": scope, "budget": budget, "used": used},
    )


def _invalid(message: str, field: str, code: str = "invalid_model_role") -> ApiError:
    return ApiError(422, code, message, {"field": field})


def _check_budget(value: int | None) -> int | None:
    if value is not None and not 0 <= value <= MAX_BUDGET:
        raise _invalid(
            "The budget must be zero or a positive number of tokens.",
            "monthly_token_budget",
            "invalid_budget",
        )
    return value


class MeteredGateway:
    """A ``Gateway`` for one role that stops when a monthly budget is used up and
    records the tokens of each call (reported, or estimated by the gateway)."""

    def __init__(
        self,
        gateway: Gateway,
        service: RoleService,
        *,
        role: str,
        model_name: str,
        workspace_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> None:
        self._gateway = gateway
        self._service = service
        self._role = role
        self._model_name = model_name
        self._workspace_id = workspace_id
        self._user_id = user_id

    @property
    def role(self) -> str:
        """The role the call is metered under (``agent`` when ``light`` fell back)."""
        return self._role

    def capabilities(self) -> Capabilities:
        return self._gateway.capabilities()

    def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        stream: bool = False,
        *,
        json_schema: dict[str, Any] | None = None,
    ) -> Iterator[ChatEvent]:
        """Like ``Gateway.chat``, but raises 429 ``token_budget_exhausted`` before the
        call when a budget is used up, and records the usage when the reply is done."""
        self._service.ensure_within_budget(self._workspace_id)
        prompt = sum(estimate_tokens(_message_text(m)) for m in messages)
        produced: list[str] = []
        reported: Usage | None = None
        try:
            for event in self._gateway.chat(messages, tools, stream, json_schema=json_schema):
                if isinstance(event, TextDelta):
                    produced.append(event.text)
                elif isinstance(event, ToolCallEvent):
                    produced.append(f"{event.call.name}{json.dumps(event.call.arguments)}")
                elif isinstance(event, Done):
                    reported = event.usage
                yield event
        finally:
            # Errors and abandoned streams count too: estimate what was sent and received.
            self._record(
                reported or Usage(prompt, estimate_tokens("".join(produced)), estimated=True)
            )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Like ``Gateway.embed``; embeddings report no usage, so the tokens are estimated."""
        self._service.ensure_within_budget(self._workspace_id)
        try:
            return self._gateway.embed(texts)
        finally:
            self._record(Usage(sum(estimate_tokens(t) for t in texts), 0, estimated=True))

    def _record(self, usage: Usage) -> None:
        self._service.record_usage(
            usage,
            role=self._role,
            model_name=self._model_name,
            workspace_id=self._workspace_id,
            user_id=self._user_id,
        )


class RoleService:
    def __init__(self, engine: sa.Engine, *, providers: ProviderService, clock: Clock) -> None:
        self._engine = engine
        self._providers = providers
        self._clock = clock

    # -- roles -----------------------------------------------------------------------

    def assignments(self) -> RoleAssignments:
        with Session(self._engine) as db:
            return _assignments(self._settings(db))

    def assign_roles(
        self,
        *,
        agent_model_id: uuid.UUID,
        light_model_id: uuid.UUID | None,
        embedding_model_id: uuid.UUID | None,
    ) -> RoleAssignments:
        """Replace the role assignments. The agent role is required; every model must be
        registered and have passed "Test connection"; chat roles take chat models and
        the embedding role an embedding model. Switching the embedding model flags that
        re-indexing is needed."""
        with Session(self._engine) as db, db.begin():
            settings = self._settings(db, lock=True)
            for role, model_id in (
                ("agent", agent_model_id),
                ("light", light_model_id),
                ("embedding", embedding_model_id),
            ):
                if model_id is not None:
                    self._check_assignable(db, role, model_id)
            now = self._clock()
            settings.agent_model_id = agent_model_id
            settings.light_model_id = light_model_id
            dimension = None
            if embedding_model_id is not None:
                chosen = db.get(ModelRecord, embedding_model_id)
                if chosen is None:
                    raise _invalid("That model is not registered.", "embedding_model_id")
                dimension = chosen.embedding_dimension
            reindex.note_assigned(settings, embedding_model_id, dimension, now)
            settings.updated_at = now
            return _assignments(settings)

    def mark_reindexed(self) -> None:
        """The re-index job finished: the flag is cleared (for document search)."""
        with Session(self._engine) as db, db.begin():
            settings = self._settings(db, lock=True)
            settings.reindex_needed = False
            settings.reindex_reason = None
            settings.reindex_flagged_at = None

    # -- using a role ----------------------------------------------------------------

    def model_for_role(self, role: str, *, light_allowed: bool = True) -> tuple[uuid.UUID, str]:
        """The model that serves ``role`` and the role it is metered under. ``light``
        falls back to the agent model when none is assigned or ``light_allowed`` is
        false (e.g. the Workspace is internal-only and the light model is external)."""
        with Session(self._engine) as db:
            settings = self._settings(db)
            model_id = {
                "agent": settings.agent_model_id,
                "light": settings.light_model_id,
                "embedding": settings.embedding_model_id,
            }.get(role)
            if role == "light" and (model_id is None or not light_allowed):
                role, model_id = "agent", settings.agent_model_id
            if model_id is None:
                raise ApiError(
                    409,
                    "model_role_unassigned",
                    f"No model is assigned to the {role} role. An admin sets it in the "
                    "AI settings.",
                    {"role": role},
                )
            return model_id, role

    def gateway_for_role(
        self,
        role: str,
        *,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        light_allowed: bool = True,
    ) -> MeteredGateway:
        model_id, role = self.model_for_role(role, light_allowed=light_allowed)
        return self.metered_gateway(model_id, role, workspace_id=workspace_id, user_id=user_id)

    def metered_gateway(
        self,
        model_id: uuid.UUID,
        role: str,
        *,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> MeteredGateway:
        """A metered gateway for a specific registered model, used under ``role``."""
        with Session(self._engine) as db:
            name = db.scalar(sa.select(ModelRecord.name).where(ModelRecord.id == model_id))
        return MeteredGateway(
            self._providers.gateway_for(model_id),
            self,
            role=role,
            model_name=name or "",
            workspace_id=workspace_id,
            user_id=user_id,
        )

    # -- budgets ---------------------------------------------------------------------

    def budgets(self) -> Budgets:
        with Session(self._engine) as db:
            rows = db.scalars(
                sa.select(WorkspaceBudgetRecord).order_by(WorkspaceBudgetRecord.workspace_id)
            )
            return Budgets(
                installation=self._settings(db).monthly_token_budget,
                workspaces=[WorkspaceBudget(r.workspace_id, r.monthly_token_budget) for r in rows],
            )

    def set_installation_budget(self, tokens: int | None) -> None:
        """Monthly tokens for the whole installation; ``None`` removes the limit."""
        tokens = _check_budget(tokens)
        with Session(self._engine) as db, db.begin():
            settings = self._settings(db, lock=True)
            settings.monthly_token_budget = tokens
            settings.updated_at = self._clock()

    def set_workspace_budget(self, workspace_id: uuid.UUID, tokens: int) -> None:
        checked = _check_budget(tokens)
        assert checked is not None
        with Session(self._engine) as db, db.begin():
            record = db.get(WorkspaceBudgetRecord, workspace_id)
            if record is None:
                db.add(
                    WorkspaceBudgetRecord(
                        workspace_id=workspace_id,
                        monthly_token_budget=checked,
                        updated_at=self._clock(),
                    )
                )
            else:
                record.monthly_token_budget = checked
                record.updated_at = self._clock()

    def clear_workspace_budget(self, workspace_id: uuid.UUID) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.delete(WorkspaceBudgetRecord).where(
                    WorkspaceBudgetRecord.workspace_id == workspace_id
                )
            )

    def ensure_within_budget(self, workspace_id: uuid.UUID | None) -> None:
        """Raise 429 ``token_budget_exhausted`` when this month's usage has reached the
        installation's budget or the Workspace's. Checked before every model call.
        Soft by design (no locking): in-flight calls may overshoot a budget."""
        start, end = parse_month(self._clock().strftime("%Y-%m"))
        with Session(self._engine) as db:
            installation = self._settings(db).monthly_token_budget
            if installation is not None:
                used = self._used(db, start, end, None)
                if used >= installation:
                    raise _budget_exhausted("installation", installation, used)
            if workspace_id is not None:
                budget = db.scalar(
                    sa.select(WorkspaceBudgetRecord.monthly_token_budget).where(
                        WorkspaceBudgetRecord.workspace_id == workspace_id
                    )
                )
                if budget is not None:
                    used = self._used(db, start, end, workspace_id)
                    if used >= budget:
                        raise _budget_exhausted("workspace", budget, used)

    # -- usage -----------------------------------------------------------------------

    def record_usage(
        self,
        usage: Usage,
        *,
        role: str,
        model_name: str,
        workspace_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> None:
        with Session(self._engine) as db, db.begin():
            db.add(
                UsageRecord(
                    id=uuid.uuid4(),
                    created_at=self._clock(),
                    role=role,
                    model_name=model_name[:MODEL_NAME_MAX_LENGTH],
                    workspace_id=workspace_id,
                    user_id=user_id,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    estimated=usage.estimated,
                )
            )

    def usage_report(self, month: str | None = None) -> UsageReport:
        """Tokens used in a month (default: this one), in total, per Workspace and per user."""
        month = month or self._clock().strftime("%Y-%m")
        start, end = parse_month(month)
        in_month = (UsageRecord.created_at >= start) & (UsageRecord.created_at < end)
        with Session(self._engine) as db:
            budget = self._settings(db).monthly_token_budget
            return UsageReport(
                month=month,
                totals=self._totals(db, in_month),
                installation_budget=budget,
                workspaces=[
                    WorkspaceUsage(key, totals, roles)
                    for key, totals, roles in self._grouped(db, in_month, UsageRecord.workspace_id)
                ],
                users=[
                    UserUsage(key, totals, roles)
                    for key, totals, roles in self._grouped(db, in_month, UsageRecord.user_id)
                ],
            )

    # -- internals -------------------------------------------------------------------

    def _settings(self, db: Session, *, lock: bool = False) -> SettingsRecord:
        query = sa.select(SettingsRecord).where(SettingsRecord.id == 1)
        record = db.scalars(query.with_for_update() if lock else query).first()
        if record is None:
            db.execute(
                sa.text(
                    "INSERT INTO llm_settings (id, reindex_needed, updated_at) "
                    "VALUES (1, false, :now) ON CONFLICT (id) DO NOTHING"
                ),
                {"now": self._clock()},
            )
            record = db.scalars(query.with_for_update() if lock else query).one()
        return record

    @staticmethod
    def _check_assignable(db: Session, role: str, model_id: uuid.UUID) -> None:
        model = db.get(ModelRecord, model_id)
        if model is None:
            raise _invalid("That model is not registered.", f"{role}_model_id")
        if (role == "embedding") != ("embedding" in model.roles):
            kind = "an embedding" if role == "embedding" else "a chat"
            raise _invalid(f"The {role} role needs {kind} model.", f"{role}_model_id")
        if model.test_ok is not True:
            raise _invalid(
                'Run "Test connection" on the model first: only a model that passed can '
                "be assigned.",
                f"{role}_model_id",
                "model_not_tested",
            )

    @staticmethod
    def _used(db: Session, start: datetime, end: datetime, workspace_id: uuid.UUID | None) -> int:
        query = sa.select(
            sa.func.coalesce(
                sa.func.sum(UsageRecord.prompt_tokens + UsageRecord.completion_tokens), 0
            )
        ).where(UsageRecord.created_at >= start, UsageRecord.created_at < end)
        if workspace_id is not None:
            query = query.where(UsageRecord.workspace_id == workspace_id)
        return int(db.scalar(query) or 0)

    @staticmethod
    def _totals(db: Session, where: sa.ColumnElement[bool]) -> UsageTotals:
        row = db.execute(_totals_query().where(where)).one()
        return UsageTotals(row.prompt, row.completion, row.calls, row.estimated)

    @staticmethod
    def _grouped(
        db: Session, where: sa.ColumnElement[bool], key: sa.Column[Any]
    ) -> list[tuple[uuid.UUID | None, UsageTotals, dict[str, int]]]:
        rows = db.execute(
            _totals_query()
            .add_columns(key.label("key"), UsageRecord.role)
            .where(where)
            .group_by(key, UsageRecord.role)
        ).all()
        grouped: dict[uuid.UUID | None, list[Any]] = {}
        for row in rows:
            grouped.setdefault(row.key, []).append(row)
        result = []
        for group_key, group in grouped.items():
            totals = UsageTotals(
                sum(r.prompt for r in group),
                sum(r.completion for r in group),
                sum(r.calls for r in group),
                sum(r.estimated for r in group),
            )
            roles = {r.role: r.prompt + r.completion for r in group}
            result.append((group_key, totals, roles))
        result.sort(key=lambda item: -item[1].total_tokens)
        return result


def _totals_query() -> sa.Select[Any]:
    return sa.select(
        sa.func.coalesce(sa.func.sum(UsageRecord.prompt_tokens), 0).label("prompt"),
        sa.func.coalesce(sa.func.sum(UsageRecord.completion_tokens), 0).label("completion"),
        sa.func.count(UsageRecord.id).label("calls"),
        sa.func.count(UsageRecord.id).filter(UsageRecord.estimated).label("estimated"),
    )


def _assignments(settings: SettingsRecord) -> RoleAssignments:
    return RoleAssignments(
        agent_model_id=settings.agent_model_id,
        light_model_id=settings.light_model_id,
        embedding_model_id=settings.embedding_model_id,
        reindex_needed=settings.reindex_needed,
        reindex_reason=settings.reindex_reason,
        reindex_flagged_at=settings.reindex_flagged_at,
    )
