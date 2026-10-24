"""Model roles, monthly token budgets and AI usage (spec §6.18, stories 157, 159, 162).

Authorization is the caller's job (the router restricts the admin endpoints to admins);
the service trusts its caller. Every model call made for a feature goes through
``gateway_for_role``, which resolves the model from the Workspace's Workspace Models: the
returned ``MeteredGateway`` checks the installation's and the Workspace's monthly budgets
before each call and records the tokens the call used.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.tracing import tracing

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
from .service import ProviderService
from .tables import (
    MODEL_NAME_MAX_LENGTH,
    ModelPriceRecord,
    ModelRecord,
    SettingsRecord,
    UsageRecord,
    WorkspaceBudgetRecord,
    WorkspaceCostBudgetRecord,
)
from .workspace_ai import default_model_id, load_settings, model_is_internal

MAX_BUDGET = 10**15
MAX_COST_BUDGET = Decimal(10**9)


@dataclass(frozen=True)
class WorkspaceBudget:
    workspace_id: uuid.UUID
    monthly_token_budget: int


@dataclass(frozen=True)
class CostBudget:
    """A monthly budget in currency; only calls priced in ``currency`` count against it."""

    currency: str
    amount: Decimal


@dataclass(frozen=True)
class WorkspaceCostBudget:
    workspace_id: uuid.UUID
    budget: CostBudget


@dataclass(frozen=True)
class Budgets:
    installation: int | None
    workspaces: list[WorkspaceBudget]
    installation_cost: CostBudget | None = None
    workspace_costs: list[WorkspaceCostBudget] = field(default_factory=list)


@dataclass(frozen=True)
class ModelPrice:
    """Per million tokens; ``cached_input`` prices the part of the prompt served from cache."""

    model_name: str
    currency: str
    input: Decimal
    output: Decimal
    cached_input: Decimal


@dataclass(frozen=True)
class Cost:
    """What calls cost, per currency, from the calls whose model has a price. Calls on a
    model without one are counted in ``unpriced_calls``: their cost is unknown, not zero."""

    amounts: dict[str, Decimal] = field(default_factory=dict)
    unpriced_calls: int = 0


def call_cost(price: ModelPrice, prompt: int, completion: int, cache_read: int) -> Decimal:
    """Cached tokens are part of the prompt; they cost the cached price, not the input price."""
    cached = min(cache_read, prompt)
    return (
        (prompt - cached) * price.input + cached * price.cached_input + completion * price.output
    ) / 1_000_000


@dataclass(frozen=True)
class UsageTotals:
    prompt_tokens: int
    completion_tokens: int
    calls: int
    estimated_calls: int
    cost: Cost = field(default_factory=Cost)

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


def _cost_budget_exhausted(scope: str, budget: CostBudget, used: Decimal) -> ApiError:
    what = "installation's" if scope == "installation" else "Workspace's"
    return ApiError(
        429,
        "cost_budget_exhausted",
        f"The {what} monthly AI budget of {budget.amount:f} {budget.currency} is used up. "
        "Everything else keeps working; ask an admin to raise the budget or wait for next month.",
        {
            "scope": scope,
            "currency": budget.currency,
            "budget": f"{budget.amount:f}",
            "used": f"{used:f}",
        },
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


def _installation_cost_budget(settings: SettingsRecord) -> CostBudget | None:
    if settings.monthly_cost_budget is None or settings.cost_budget_currency is None:
        return None
    return CostBudget(settings.cost_budget_currency, settings.monthly_cost_budget)


def _check_cost_budget(budget: CostBudget) -> None:
    if not Decimal(0) <= budget.amount <= MAX_COST_BUDGET:
        raise _invalid(
            "The budget must be zero or a positive amount.",
            "amount",
            "invalid_budget",
        )


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
        refuse_external: bool = False,
    ) -> None:
        self._gateway = gateway
        self._refuse_external = refuse_external
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

    def count_tokens(self, text: str) -> int:
        """Like ``Gateway.count_tokens``; an internal-only Workspace never reaches an
        external provider's count endpoint, so it gets the estimate."""
        return estimate_tokens(text) if self._refuse_external else self._gateway.count_tokens(text)

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
        self._ensure_allowed()
        produced: list[str] = []
        reported: Usage | None = None
        with tracing().model_call(self._model_name, self._role) as span:
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
                if reported is None:
                    prompt = sum(estimate_tokens(_message_text(m)) for m in messages)
                    reported = Usage(prompt, estimate_tokens("".join(produced)), estimated=True)
                self._record(reported)
                span.usage(reported.prompt_tokens, reported.completion_tokens)
                if tracing().capture_content:
                    span.content("\n".join(_message_text(m) for m in messages), "".join(produced))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Like ``Gateway.embed``; embeddings report no usage, so the tokens are estimated."""
        self._ensure_allowed()
        try:
            return self._gateway.embed(texts)
        finally:
            self._record(Usage(sum(estimate_tokens(t) for t in texts), 0, estimated=True))

    def _ensure_allowed(self) -> None:
        """Refuse an external provider for an internal-only Workspace, whatever the role,
        then check the budgets."""
        if self._refuse_external:
            raise ApiError(
                409,
                "external_provider_refused",
                "This Workspace is internal-only, and the model for this task runs on an "
                "external provider. An owner can change the Workspace's AI settings.",
                {"role": self._role, "model": self._model_name},
            )
        self._service.ensure_within_budget(self._workspace_id)

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

    # -- using a role ----------------------------------------------------------------

    def gateway_for_role(
        self, role: str, *, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> MeteredGateway:
        """The gateway a feature calls for ``role``, resolved from the Workspace's
        Workspace Models: ``agent`` its default agent model, ``light`` its default light
        model (else, metered as ``agent``, its default agent model) and ``embedding`` its
        embedding model. Every call is made for a Workspace; nothing falls back to an
        installation-wide model. 409 ``model_role_unassigned`` when the Workspace has none.
        Whatever the role, an external model is refused for an internal-only Workspace when
        the call is made."""
        with Session(self._engine) as db:
            model_id = default_model_id(db, workspace_id, role)
            if role == "light" and model_id is None:
                role, model_id = "agent", default_model_id(db, workspace_id, "agent")
        if model_id is None:
            raise ApiError(
                409,
                "model_role_unassigned",
                f"This Workspace has no {role} model. An owner chooses it in the "
                "Workspace's AI settings.",
                {"role": role},
            )
        return self.metered_gateway(model_id, role, workspace_id=workspace_id, user_id=user_id)

    def light_gateway(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> MeteredGateway | None:
        """The gateway of a light-role feature, or ``None`` when the Workspace has no light
        model: such a feature is then off. Unlike ``gateway_for_role("light")`` it never
        falls back to the agent model."""
        with Session(self._engine) as db:
            model_id = default_model_id(db, workspace_id, "light")
        if model_id is None:
            return None
        return self.metered_gateway(model_id, "light", workspace_id=workspace_id, user_id=user_id)

    def metered_gateway(
        self,
        model_id: uuid.UUID,
        role: str,
        *,
        workspace_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> MeteredGateway:
        """A metered gateway for a specific registered model, used under ``role``. For an
        internal-only Workspace its calls are refused when the model's provider is external."""
        with Session(self._engine) as db:
            name = db.scalar(sa.select(ModelRecord.name).where(ModelRecord.id == model_id))
            refuse = (
                workspace_id is not None
                and load_settings(db, workspace_id).internal_only
                and not model_is_internal(db, model_id)
            )
        return MeteredGateway(
            self._providers.gateway_for(model_id),
            self,
            role=role,
            model_name=name or "",
            workspace_id=workspace_id,
            user_id=user_id,
            refuse_external=refuse,
        )

    # -- budgets ---------------------------------------------------------------------

    def budgets(self) -> Budgets:
        with Session(self._engine) as db:
            rows = db.scalars(
                sa.select(WorkspaceBudgetRecord).order_by(WorkspaceBudgetRecord.workspace_id)
            )
            settings = self._settings(db)
            costs = db.scalars(
                sa.select(WorkspaceCostBudgetRecord).order_by(
                    WorkspaceCostBudgetRecord.workspace_id
                )
            )
            return Budgets(
                installation=settings.monthly_token_budget,
                workspaces=[WorkspaceBudget(r.workspace_id, r.monthly_token_budget) for r in rows],
                installation_cost=_installation_cost_budget(settings),
                workspace_costs=[
                    WorkspaceCostBudget(
                        r.workspace_id, CostBudget(r.currency, r.monthly_cost_budget)
                    )
                    for r in costs
                ],
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

    def set_installation_cost_budget(self, budget: CostBudget | None) -> None:
        """Monthly currency budget for the whole installation; ``None`` removes it."""
        if budget is not None:
            _check_cost_budget(budget)
        with Session(self._engine) as db, db.begin():
            settings = self._settings(db, lock=True)
            settings.monthly_cost_budget = budget.amount if budget else None
            settings.cost_budget_currency = budget.currency if budget else None
            settings.updated_at = self._clock()

    def set_workspace_cost_budget(self, workspace_id: uuid.UUID, budget: CostBudget) -> None:
        _check_cost_budget(budget)
        with Session(self._engine) as db, db.begin():
            record = db.get(WorkspaceCostBudgetRecord, workspace_id)
            if record is None:
                record = WorkspaceCostBudgetRecord(workspace_id=workspace_id)
                db.add(record)
            record.monthly_cost_budget = budget.amount
            record.currency = budget.currency
            record.updated_at = self._clock()

    def clear_workspace_cost_budget(self, workspace_id: uuid.UUID) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.delete(WorkspaceCostBudgetRecord).where(
                    WorkspaceCostBudgetRecord.workspace_id == workspace_id
                )
            )

    def ensure_within_budget(self, workspace_id: uuid.UUID | None) -> None:
        """Raise 429 ``token_budget_exhausted`` (or ``cost_budget_exhausted`` for a budget
        in currency) when this month's usage has reached the installation's budget or the
        Workspace's: whichever is reached first stops AI use. Checked before every model
        call. Soft by design (no locking): in-flight calls may overshoot a budget."""
        start, end = parse_month(self._clock().strftime("%Y-%m"))
        with Session(self._engine) as db:
            settings = self._settings(db)
            installation = settings.monthly_token_budget
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
            self._ensure_within_cost_budgets(db, settings, workspace_id, start, end)

    def _ensure_within_cost_budgets(
        self,
        db: Session,
        settings: SettingsRecord,
        workspace_id: uuid.UUID | None,
        start: datetime,
        end: datetime,
    ) -> None:
        in_month = (UsageRecord.created_at >= start) & (UsageRecord.created_at < end)
        installation = _installation_cost_budget(settings)
        if installation is not None:
            used = self._spent(db, in_month, installation.currency)
            if used >= installation.amount:
                raise _cost_budget_exhausted("installation", installation, used)
        if workspace_id is not None:
            record = db.get(WorkspaceCostBudgetRecord, workspace_id)
            if record is not None:
                budget = CostBudget(record.currency, record.monthly_cost_budget)
                used = self._spent(
                    db, in_month & (UsageRecord.workspace_id == workspace_id), budget.currency
                )
                if used >= budget.amount:
                    raise _cost_budget_exhausted("workspace", budget, used)

    @classmethod
    def _spent(cls, db: Session, where: sa.ColumnElement[bool], currency: str) -> Decimal:
        """What the matching calls cost in ``currency`` (calls priced otherwise, or not at
        all, don't count)."""
        return cls._costs(db, where, None).get(0, Cost()).amounts.get(currency, Decimal(0))

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
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
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

    # -- model prices ----------------------------------------------------------------

    def prices(self) -> list[ModelPrice]:
        with Session(self._engine) as db:
            return list(self._price_map(db).values())

    def set_price(self, price: ModelPrice) -> None:
        with Session(self._engine) as db, db.begin():
            record = db.get(ModelPriceRecord, price.model_name)
            if record is None:
                record = ModelPriceRecord(model_name=price.model_name)
                db.add(record)
            record.currency = price.currency
            record.input_per_million = price.input
            record.output_per_million = price.output
            record.cached_input_per_million = price.cached_input
            record.updated_at = self._clock()

    def clear_price(self, model_name: str) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(sa.delete(ModelPriceRecord).where(ModelPriceRecord.model_name == model_name))

    # -- internals -------------------------------------------------------------------

    def _settings(self, db: Session, *, lock: bool = False) -> SettingsRecord:
        query = sa.select(SettingsRecord).where(SettingsRecord.id == 1)
        record = db.scalars(query.with_for_update() if lock else query).first()
        if record is None:
            db.execute(
                sa.text(
                    "INSERT INTO llm_settings (id, updated_at) "
                    "VALUES (1, :now) ON CONFLICT (id) DO NOTHING"
                ),
                {"now": self._clock()},
            )
            record = db.scalars(query.with_for_update() if lock else query).one()
        return record

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
    def _price_map(db: Session) -> dict[str, ModelPrice]:
        rows = db.scalars(sa.select(ModelPriceRecord).order_by(ModelPriceRecord.model_name))
        return {
            r.model_name: ModelPrice(
                r.model_name,
                r.currency,
                r.input_per_million,
                r.output_per_million,
                r.cached_input_per_million,
            )
            for r in rows
        }

    @classmethod
    def _costs(
        cls, db: Session, where: sa.ColumnElement[bool], key: sa.ColumnElement[Any] | None
    ) -> dict[Any, Cost]:
        """The cost of the calls matching ``where``, per ``key`` (None: one entry, key 0)."""
        prices = cls._price_map(db)
        key_column = (key if key is not None else sa.literal(0)).label("key")
        rows = db.execute(
            sa.select(
                key_column,
                UsageRecord.model_name,
                sa.func.sum(UsageRecord.prompt_tokens).label("prompt"),
                sa.func.sum(UsageRecord.completion_tokens).label("completion"),
                sa.func.sum(UsageRecord.cache_read_tokens).label("cache_read"),
                sa.func.count(UsageRecord.id).label("calls"),
            )
            .where(where)
            .group_by(key_column, UsageRecord.model_name)
        ).all()
        amounts: dict[Any, dict[str, Decimal]] = {}
        unpriced: dict[Any, int] = {}
        for row in rows:
            amounts.setdefault(row.key, {})
            unpriced.setdefault(row.key, 0)
            price = prices.get(row.model_name)
            if price is None:
                unpriced[row.key] += row.calls
                continue
            cost = call_cost(price, row.prompt, row.completion, row.cache_read)
            amounts[row.key][price.currency] = amounts[row.key].get(price.currency, 0) + cost
        return {k: Cost(amounts[k], unpriced[k]) for k in amounts}

    @classmethod
    def _totals(cls, db: Session, where: sa.ColumnElement[bool]) -> UsageTotals:
        row = db.execute(_totals_query().where(where)).one()
        cost = cls._costs(db, where, None).get(0, Cost())
        return UsageTotals(row.prompt, row.completion, row.calls, row.estimated, cost)

    @classmethod
    def _grouped(
        cls, db: Session, where: sa.ColumnElement[bool], key: sa.Column[Any]
    ) -> list[tuple[uuid.UUID | None, UsageTotals, dict[str, int]]]:
        rows = db.execute(
            _totals_query()
            .add_columns(key.label("key"), UsageRecord.role)
            .where(where)
            .group_by(key, UsageRecord.role)
        ).all()
        costs = cls._costs(db, where, key)
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
                costs.get(group_key, Cost()),
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
