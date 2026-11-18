"""The admin explorer of background AI (#631): the model calls DAWAM made outside the assistant,
one row per background job and, since a search has no job, one per Workspace per day of
document searches (admins only).

Everything comes from the ``llm_usage`` rows the calls left (their feature, job, model, tokens,
duration and outcome) and from the job they were made for; no prompt or output is stored, so
none is shown. A cost is the calls' tokens at their models' prices, unknown (not zero) when a
model has no price, the same rule as the assistant's run explorer.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.jobs import Job
from dawam.modules.workspaces import INSTALLATION, Action, WorkspaceService, can
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .call_context import FEATURES, UNKNOWN_FEATURE
from .role_service import ModelPrice, RoleService, call_cost
from .tables import UsageRecord

JOB_LIST_LIMIT = 100
FAILED_CALLS_LIMIT = 100
LISTED_FEATURES = (*(f for f in FEATURES if f != "assistant"), UNKNOWN_FEATURE)
"""The assistant's own calls are its runs, in the run explorer."""
JOB_STATUSES = ("completed", "partial", "failed", "running")
SEARCH = "search"

JobLookup = Callable[[Collection[uuid.UUID]], Mapping[uuid.UUID, Job]]
UserLookup = Callable[[uuid.UUID], User | None]


@dataclass(frozen=True)
class AiCost:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class AiJobSummary:
    key: str
    """The job's id, or ``search:<workspace id>:<UTC day>`` for a day of searches."""
    feature: str
    target: str | None
    """What the job worked on (its title, with the number of tables when it names them)."""
    job_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    workspace_name: str | None
    started_by: str
    """The member's email, ``Scheduled`` for a job nobody started, or for searches the
    number of members who searched."""
    member_count: int | None
    started_at: datetime
    status: str
    """``running``, ``completed``, ``partial`` (some calls failed) or ``failed``."""
    calls: int
    failed_calls: int
    duration_ms: int
    input_tokens: int
    output_tokens: int
    cost: AiCost | None
    models: list[str]


@dataclass(frozen=True)
class ModelCalls:
    role: str
    model_name: str
    calls: int
    failed_calls: int
    avg_latency_ms: int | None
    input_tokens: int
    output_tokens: int
    cost: AiCost | None


@dataclass(frozen=True)
class FailedCall:
    at: datetime
    error_code: str | None
    role: str
    model_name: str


@dataclass(frozen=True)
class AiJobDetail:
    summary: AiJobSummary
    models: list[ModelCalls]
    failed_calls: list[FailedCall]


@dataclass(frozen=True)
class _Key:
    job_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    day: date | None

    @classmethod
    def of(cls, job_id: uuid.UUID | None, workspace_id: uuid.UUID | None, day: date | None) -> _Key:
        """A job is its id alone; a day of searches is its Workspace and day."""
        return cls(job_id, None, None) if job_id is not None else cls(None, workspace_id, day)

    def text(self) -> str:
        if self.job_id is not None:
            return str(self.job_id)
        return f"{SEARCH}:{self.workspace_id or 'none'}:{self.day}"

    @classmethod
    def parse(cls, key: str) -> _Key | None:
        try:
            if not key.startswith(f"{SEARCH}:"):
                return cls(uuid.UUID(key), None, None)
            _, workspace, day = key.split(":")
            workspace_id = None if workspace == "none" else uuid.UUID(workspace)
            return cls(None, workspace_id, date.fromisoformat(day))
        except ValueError:
            return None


class AiJobExplorer:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        roles: RoleService,
        jobs: JobLookup,
        users: UserLookup,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._roles = roles
        self._jobs = jobs
        self._users = users
        self._clock = clock

    def list_jobs(
        self,
        user: User,
        *,
        workspace_id: uuid.UUID | None = None,
        feature: str | None = None,
        status: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
    ) -> list[AiJobSummary]:
        """Newest first, at most ``JOB_LIST_LIMIT``; 403 unless an admin."""
        _require_admin(user)
        if feature is not None and feature not in LISTED_FEATURES:
            raise ApiError(422, "invalid_feature", "That is not a feature of background AI.")
        if status is not None and status not in JOB_STATUSES:
            raise ApiError(422, "invalid_status", "That is not a job status.")
        where: list[sa.ColumnElement[bool]] = []
        if workspace_id is not None:
            where.append(UsageRecord.workspace_id == workspace_id)
        if feature is not None:
            where.append(UsageRecord.feature == feature)
        calls = _calls(*where)
        started = sa.func.min(calls.c.created_at)
        query = (
            sa.select(calls.c.job_id, calls.c.workspace_id, calls.c.day)
            .group_by(calls.c.job_id, calls.c.workspace_id, calls.c.day)
            .order_by(started.desc(), calls.c.job_id, calls.c.workspace_id, calls.c.day)
        )
        if started_from is not None:
            query = query.having(started >= started_from)
        if started_to is not None:
            query = query.having(started < started_to)
        if status is None:  # else the status, known only once the job is read, filters below
            query = query.limit(JOB_LIST_LIMIT)
        with Session(self._engine) as db:
            keys = list(dict.fromkeys(_Key.of(*row) for row in db.execute(query)))
            summaries = [s for s, _ in self._read(db, keys)]
        if status is not None:
            summaries = [s for s in summaries if s.status == status]
        return summaries[:JOB_LIST_LIMIT]

    def get_job(self, user: User, key: str) -> AiJobDetail:
        """The job (or day of searches) with its calls per role and model and its failed
        calls; 404 if it made no model call."""
        _require_admin(user)
        parsed = _Key.parse(key)
        with Session(self._engine) as db:
            found = self._read(db, [parsed]) if parsed is not None else []
            if not found:
                raise ApiError(404, "not_found", "No model call was made for that job.")
            [(summary, models)] = found
            calls = _calls()
            failed = db.execute(
                sa.select(calls)
                .where(_matching([parsed], calls), calls.c.status == "failed")
                .order_by(calls.c.created_at, calls.c.id)
                .limit(FAILED_CALLS_LIMIT)
            ).all()
        return AiJobDetail(
            summary,
            models,
            [FailedCall(r.created_at, r.error_code, r.role, r.model_name) for r in failed],
        )

    def _read(self, db: Session, keys: list[_Key]) -> list[tuple[AiJobSummary, list[ModelCalls]]]:
        """Each key's summary and calls per role and model, in the order of ``keys``; a key
        without calls is left out."""
        if not keys:
            return []
        calls = _calls()
        group = (calls.c.job_id, calls.c.workspace_id, calls.c.day)
        rows = db.execute(
            sa.select(
                *group,
                calls.c.feature,
                calls.c.role,
                calls.c.model_name,
                sa.func.count().label("calls"),
                sa.func.count().filter(calls.c.status == "failed").label("failed"),
                sa.func.sum(calls.c.prompt_tokens).label("prompt"),
                sa.func.sum(calls.c.completion_tokens).label("completion"),
                sa.func.sum(calls.c.cache_read_tokens).label("cache_read"),
                sa.func.avg(calls.c.duration_ms).label("latency"),
                sa.func.coalesce(sa.func.sum(calls.c.duration_ms), 0).label("duration"),
                sa.func.min(calls.c.created_at).label("started"),
            )
            .where(_matching(keys, calls))
            .group_by(*group, calls.c.feature, calls.c.role, calls.c.model_name)
        ).all()
        members = {
            _Key.of(r.job_id, r.workspace_id, r.day): r.members
            for r in db.execute(
                sa.select(*group, sa.func.count(sa.distinct(calls.c.user_id)).label("members"))
                .where(_matching(keys, calls))
                .group_by(*group)
            )
        }
        by_key: dict[_Key, list[Any]] = {}
        for row in rows:
            by_key.setdefault(_Key.of(row.job_id, row.workspace_id, row.day), []).append(row)
        jobs = self._jobs({k.job_id for k in keys if k.job_id is not None})
        prices = {p.model_name: p for p in self._roles.prices()}
        workspace_ids = {k.workspace_id for k in by_key} | {j.workspace_id for j in jobs.values()}
        names = self._workspaces.names_by_id(w for w in workspace_ids if w is not None)
        out = []
        for key in keys:
            if key not in by_key:
                continue
            job = jobs.get(key.job_id) if key.job_id is not None else None
            models = [_model_calls(r, prices.get(r.model_name)) for r in by_key[key]]
            models.sort(key=lambda m: (-m.calls, m.role, m.model_name))
            summary = self._summary(key, job, by_key[key], models, members.get(key, 0), names)
            out.append((summary, models))
        return out

    def _summary(
        self,
        key: _Key,
        job: Job | None,
        rows: list[Any],
        models: list[ModelCalls],
        members: int,
        names: Mapping[uuid.UUID, str],
    ) -> AiJobSummary:
        calls = sum(r.calls for r in rows)
        failed = sum(r.failed for r in rows)
        first_call = min(r.started for r in rows)
        workspace_id = job.workspace_id if job is not None else key.workspace_id
        if job is not None and job.started_at is not None:
            ended = job.finished_at or self._clock()
            duration = round((ended - job.started_at) / timedelta(milliseconds=1))
        else:
            duration = int(sum(r.duration for r in rows))
        if key.job_id is None:
            started_by = f"{members} member" + ("" if members == 1 else "s")
        else:
            started_by = self._started_by(job)
        return AiJobSummary(
            key=key.text(),
            feature=max(rows, key=lambda r: r.calls).feature,
            target=_target(job),
            job_id=key.job_id,
            workspace_id=workspace_id,
            workspace_name=names.get(workspace_id) if workspace_id else None,
            started_by=started_by,
            member_count=members if key.job_id is None else None,
            started_at=job.started_at if job is not None and job.started_at else first_call,
            status=_status(job, calls, failed),
            calls=calls,
            failed_calls=failed,
            duration_ms=max(duration, 0),
            input_tokens=int(sum(r.prompt for r in rows)),
            output_tokens=int(sum(r.completion for r in rows)),
            cost=_total([m.cost for m in models]),
            models=list(dict.fromkeys(m.model_name for m in models)),
        )

    def _started_by(self, job: Job | None) -> str:
        """The member who started the job (its creator, else the member it names in its
        params), else ``Scheduled``."""
        if job is None:
            return "Scheduled"
        candidates = [job.created_by, *(_param_id(job, k) for k in ("user_id", "requested_by"))]
        for user_id in candidates:
            if user_id is not None and (member := self._users(user_id)) is not None:
                return member.email
        return "Scheduled"


def _require_admin(user: User) -> None:
    if not can(user, Action.VIEW_ASSISTANT_RUNS, INSTALLATION):
        raise ApiError(403, "forbidden", "Only admins can do this.")


def _calls(*where: sa.ColumnElement[bool]) -> sa.Subquery:
    """The calls the explorer lists: a background job's (not the assistant's, which are its
    runs) and searches', each with its UTC ``day`` when it has no job."""
    day = sa.cast(sa.func.timezone(sa.literal_column("'UTC'"), UsageRecord.created_at), sa.Date)
    return (
        sa.select(
            *UsageRecord.__table__.c,
            sa.case((UsageRecord.job_id.is_(None), day), else_=sa.null()).label("day"),
        )
        .where(
            sa.or_(
                UsageRecord.job_id.is_not(None) & (UsageRecord.feature != "assistant"),
                UsageRecord.job_id.is_(None) & (UsageRecord.feature == SEARCH),
            ),
            *where,
        )
        .subquery()
    )


def _matching(keys: Iterable[_Key], calls: sa.Subquery) -> sa.ColumnElement[bool]:
    """The calls of ``keys``."""
    keys = list(keys)
    conditions: list[sa.ColumnElement[bool]] = []
    job_ids = [k.job_id for k in keys if k.job_id is not None]
    if job_ids:
        conditions.append(calls.c.job_id.in_(job_ids))
    for k in keys:
        if k.job_id is not None or k.day is None:
            continue
        start = datetime.combine(k.day, time(), tzinfo=UTC)
        workspace = (
            calls.c.workspace_id.is_(None)
            if k.workspace_id is None
            else calls.c.workspace_id == k.workspace_id
        )
        conditions.append(
            sa.and_(
                calls.c.job_id.is_(None),
                workspace,
                calls.c.created_at >= start,
                calls.c.created_at < start + timedelta(days=1),
            )
        )
    return sa.or_(*conditions) if conditions else sa.false()


def _model_calls(row: Any, price: ModelPrice | None) -> ModelCalls:
    cost = (
        AiCost(call_cost(price, row.prompt, row.completion, row.cache_read), price.currency)
        if price is not None
        else None
    )
    return ModelCalls(
        role=row.role,
        model_name=row.model_name,
        calls=row.calls,
        failed_calls=row.failed,
        avg_latency_ms=round(row.latency) if row.latency is not None else None,
        input_tokens=int(row.prompt),
        output_tokens=int(row.completion),
        cost=cost,
    )


def _total(costs: list[AiCost | None]) -> AiCost | None:
    """The sum, unknown when a model has no price (or the prices are in different currencies)."""
    if not costs or any(c is None for c in costs):
        return None
    currencies = {c.currency for c in costs if c is not None}
    if len(currencies) != 1:
        return None
    return AiCost(sum((c.amount for c in costs if c is not None), Decimal(0)), currencies.pop())


def _status(job: Job | None, calls: int, failed: int) -> str:
    if job is not None and job.status in ("queued", "running", "paused"):
        return "running"
    if job is not None and job.status in ("failed", "cancelled"):
        return "failed"
    if failed == 0:
        return "completed"
    return "failed" if failed == calls else "partial"


def _target(job: Job | None) -> str | None:
    if job is None:
        return None
    tables = (job.params or {}).get("table_ids")
    if isinstance(tables, list) and tables:
        return f"{job.title} · {len(tables)} table" + ("" if len(tables) == 1 else "s")
    return job.title


def _param_id(job: Job, name: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str((job.params or {})[name]))
    except (KeyError, ValueError):
        return None
