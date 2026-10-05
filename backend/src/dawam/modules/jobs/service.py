"""Background jobs: a Postgres-backed queue (``SELECT ... FOR UPDATE SKIP LOCKED``).

``JobService.submit`` stores a ``queued`` job; the worker process claims jobs with
``claim_next`` and runs them with ``execute``. In tests the runner is inline, so
``submit`` runs the job before it returns.

A handler is ``handler(payload, ctx)``: it reports ``ctx.progress(percent)`` and
``ctx.log(line)`` and should call ``ctx.raise_if_cancelled()`` between steps. Returning
means the job succeeded; raising fails it with the exception's text as the reason.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.notifications import NotificationService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError
from dawam.platform.pagination import DEFAULT_PAGE_SIZE, decode_cursor, encode_cursor

from .tables import (
    ERROR_MAX_LENGTH,
    KIND_MAX_LENGTH,
    LOG_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    JobRecord,
)

logger = logging.getLogger("dawam.jobs")

STALE_AFTER = timedelta(seconds=60)
"""A running job whose worker has not reported for this long lost its worker."""
WORKER_LOST = "The worker running this job stopped before it finished."

ACTIVE = ("queued", "running")


class UnknownJobKindError(LookupError):
    pass


class JobCancelledError(Exception):
    """Raised by ``JobContext.raise_if_cancelled``; ends the handler quietly."""


class JobContext(Protocol):
    def progress(self, percent: int) -> None:
        """Report how far along the job is (0-100); also tells the queue it is alive."""
        ...

    def log(self, message: str) -> None:
        """Append a line to the job's log."""
        ...

    @property
    def cancelled(self) -> bool:
        """Whether somebody cancelled the job (or its Workspace was archived)."""
        ...

    def raise_if_cancelled(self) -> None: ...


JobHandler = Callable[[Mapping[str, Any], JobContext], None]


class JobRunner(Protocol):
    """The handlers by job kind, and whether ``submit`` runs a job right away."""

    inline: bool

    def register(self, kind: str, handler: JobHandler) -> None:
        """Make ``handler`` run jobs of ``kind``. Called once per kind at startup."""
        ...

    def handler(self, kind: str) -> JobHandler:
        """The handler of ``kind``; ``UnknownJobKindError`` if none is registered."""
        ...


class _Handlers:
    inline = False

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, kind: str, handler: JobHandler) -> None:
        if kind in self._handlers:
            raise ValueError(f"a handler for job kind {kind!r} is already registered")
        if len(kind) > KIND_MAX_LENGTH:
            raise ValueError(f"job kind {kind!r} is longer than {KIND_MAX_LENGTH} characters")
        self._handlers[kind] = handler

    def handler(self, kind: str) -> JobHandler:
        try:
            return self._handlers[kind]
        except KeyError:
            raise UnknownJobKindError(kind) from None


class InlineJobRunner(_Handlers):
    """Runs each job in the submitting thread, before ``submit`` returns (tests)."""

    inline = True


class QueuedJobRunner(_Handlers):
    """Leaves jobs ``queued`` for the worker process to claim."""

    inline = False


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    title: str
    status: str
    """``queued``, ``running``, ``succeeded``, ``failed`` or ``cancelled``."""
    progress: int
    log: str
    error: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class JobPage:
    items: list[Job]
    next_cursor: str | None


def _view(record: JobRecord) -> Job:
    return Job(
        id=record.id,
        workspace_id=record.workspace_id,
        kind=record.kind,
        title=record.title,
        status=record.status,
        progress=record.progress,
        log=record.log,
        error=record.error,
        created_by=record.created_by,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Job not found.")


class _Context:
    def __init__(self, service: JobService, job_id: uuid.UUID) -> None:
        self._service = service
        self._job_id = job_id

    def progress(self, percent: int) -> None:
        self._service._update(self._job_id, progress=max(0, min(100, int(percent))))

    def log(self, message: str) -> None:
        self._service._append_log(self._job_id, message)

    @property
    def cancelled(self) -> bool:
        with Session(self._service._engine) as db:
            status = db.scalar(sa.select(JobRecord.status).where(JobRecord.id == self._job_id))
        return status != "running"

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError


class JobService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        runner: JobRunner,
        clock: Clock,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self._engine = engine
        self._runner = runner
        self._clock = clock
        self._stale_after = stale_after
        self._notifications = NotificationService(engine, clock=clock)

    # -- for other modules ---------------------------------------------------------

    def submit(
        self,
        workspace_id: uuid.UUID,
        kind: str,
        payload: Mapping[str, Any],
        *,
        title: str,
        created_by: uuid.UUID | None,
    ) -> Job:
        """Queue a job of ``kind`` in the Workspace and return it. The caller has
        authorized ``created_by`` already. ``payload`` is JSON the handler gets back.
        ``UnknownJobKindError`` if no handler is registered for ``kind``. With the inline
        runner the job has run (and is finished) when this returns."""
        self._runner.handler(kind)
        record = JobRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            kind=kind,
            title=title.strip()[:TITLE_MAX_LENGTH] or kind,
            payload=dict(payload),
            status="queued",
            progress=0,
            log="",
            created_by=created_by,
            created_at=self._clock(),
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            job_id = record.id
        if self._runner.inline:
            claimed = self._claim(job_id)
            if claimed is not None:
                self.execute(claimed)
        return self._load_view(job_id)

    def cancel_for_workspace(self, db: Session, workspace_id: uuid.UUID, at: datetime) -> None:
        """Cancel the Workspace's queued and running jobs, in ``db``'s transaction (the
        archive hook). A running handler notices at its next ``raise_if_cancelled``."""
        db.execute(
            sa.update(JobRecord)
            .where(JobRecord.workspace_id == workspace_id, JobRecord.status.in_(ACTIVE))
            .values(status="cancelled", finished_at=at)
        )

    # -- for users -----------------------------------------------------------------

    def get(self, user: User, job_id: uuid.UUID) -> Job:
        """Open a job: any member of its Workspace; 404 for everyone else."""
        with Session(self._engine) as db:
            record = self._record(db, job_id)
            self._workspaces().authorize(user, Action.VIEW_WORKSPACE, record.workspace_id)
            return _view(record)

    def list(
        self,
        user: User,
        workspace_id: uuid.UUID,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> JobPage:
        """The Workspace's jobs, newest first, for any member."""
        self._workspaces().authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        query = (
            sa.select(JobRecord)
            .where(JobRecord.workspace_id == workspace_id)
            .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            .limit(limit + 1)
        )
        if cursor is not None:
            after_at, after_id = decode_cursor(cursor, 2)
            try:
                key = (datetime.fromisoformat(after_at), uuid.UUID(after_id))
            except ValueError:
                raise ApiError(422, "invalid_cursor", "The cursor is not valid.") from None
            query = query.where(sa.tuple_(JobRecord.created_at, JobRecord.id) < sa.tuple_(*key))
        with Session(self._engine) as db:
            records = list(db.scalars(query))
        last = records[limit - 1] if len(records) > limit else None
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id)) if last else None
        return JobPage(items=[_view(r) for r in records[:limit]], next_cursor=next_cursor)

    def cancel(self, user: User, job_id: uuid.UUID) -> Job:
        """Cancel a queued or running job: its creator may, and so may an owner of its
        Workspace. 409 ``job_finished`` if it has ended already."""
        with Session(self._engine) as db, db.begin():
            record = self._record(db, job_id, lock=True)
            action = (
                Action.CANCEL_OWN_JOB if record.created_by == user.id else Action.CANCEL_ANY_JOB
            )
            self._workspaces().authorize(user, action, record.workspace_id)
            if record.status not in ACTIVE:
                raise ApiError(409, "job_finished", f"This job has already {record.status}.", {})
            record.status = "cancelled"
            record.finished_at = self._clock()
            db.flush()
            return _view(record)

    # -- for the worker --------------------------------------------------------------

    def claim_next(self) -> Job | None:
        """Take the oldest queued job (``FOR UPDATE SKIP LOCKED``, so workers never take
        the same one) and mark it running; ``None`` if there is none."""
        return self._claim(None)

    def execute(self, job: Job) -> None:
        """Run a claimed job's handler and record how it ended. A job cancelled meanwhile
        keeps its ``cancelled`` status."""
        ctx = _Context(self, job.id)
        with Session(self._engine) as db:
            payload = db.scalar(sa.select(JobRecord.payload).where(JobRecord.id == job.id)) or {}
        try:
            self._runner.handler(job.kind)(payload, ctx)
        except JobCancelledError:
            return
        except UnknownJobKindError:
            self._finish(job.id, "failed", f"No handler is registered for {job.kind!r} jobs.")
        except Exception as exc:  # a handler may fail in any way; the job records it
            logger.exception("job failed", extra={"job_id": str(job.id), "kind": job.kind})
            self._finish(job.id, "failed", str(exc) or type(exc).__name__)
        else:
            self._finish(job.id, "succeeded", None)

    def heartbeat(self, job_id: uuid.UUID) -> None:
        """Tell the queue the worker is still running ``job_id``."""
        self._update(job_id)

    def fail_lost(self) -> int:
        """Fail running jobs whose worker stopped reporting (it died or was restarted);
        returns how many."""
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            lost = list(
                db.scalars(
                    sa.select(JobRecord)
                    .where(
                        JobRecord.status == "running",
                        JobRecord.heartbeat_at < now - self._stale_after,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for record in lost:
                self._end(db, record, "failed", WORKER_LOST, now)
        return len(lost)

    # -- internals -------------------------------------------------------------------

    def _workspaces(self) -> WorkspaceService:
        return WorkspaceService(self._engine, clock=self._clock)

    def _record(self, db: Session, job_id: uuid.UUID, *, lock: bool = False) -> JobRecord:
        query = sa.select(JobRecord).where(JobRecord.id == job_id)
        if lock:
            query = query.with_for_update()
        record = db.scalars(query).first()
        if record is None:
            raise _not_found()
        return record

    def _load_view(self, job_id: uuid.UUID) -> Job:
        with Session(self._engine) as db:
            return _view(self._record(db, job_id))

    def _claim(self, job_id: uuid.UUID | None) -> Job | None:
        query = sa.select(JobRecord).where(JobRecord.status == "queued")
        if job_id is not None:
            query = query.where(JobRecord.id == job_id)
        query = query.order_by(JobRecord.created_at, JobRecord.id).limit(1)
        with Session(self._engine) as db, db.begin():
            record = db.scalars(query.with_for_update(skip_locked=True)).first()
            if record is None:
                return None
            now = self._clock()
            record.status = "running"
            record.started_at = now
            record.heartbeat_at = now
            db.flush()
            return _view(record)

    def _update(self, job_id: uuid.UUID, **values: Any) -> None:
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == "running")
                .values(heartbeat_at=self._clock(), **values)
            )

    def _append_log(self, job_id: uuid.UUID, message: str) -> None:
        line = f"{self._clock().isoformat()} {message.rstrip()}\n"
        with Session(self._engine) as db, db.begin():
            db.execute(
                sa.update(JobRecord)
                .where(JobRecord.id == job_id, JobRecord.status == "running")
                .values(
                    heartbeat_at=self._clock(),
                    log=sa.func.right(JobRecord.log + line, LOG_MAX_LENGTH),
                )
            )

    def _finish(self, job_id: uuid.UUID, status: str, error: str | None) -> None:
        with Session(self._engine) as db, db.begin():
            record = self._record(db, job_id, lock=True)
            if record.status == "running":
                self._end(db, record, status, error, self._clock())

    def _end(
        self, db: Session, record: JobRecord, status: str, error: str | None, at: datetime
    ) -> None:
        """Mark a running job ``succeeded`` or ``failed`` and tell its creator."""
        record.status = status
        record.error = error[:ERROR_MAX_LENGTH] if error else None
        record.finished_at = at
        if status == "succeeded":
            record.progress = 100
        if record.created_by is not None:
            message = (
                f'Job "{record.title}" finished.'
                if status == "succeeded"
                else f'Job "{record.title}" failed: {record.error}'
            )
            self._notifications.notify(
                [record.created_by],
                kind="job",
                message=message,
                workspace_id=record.workspace_id,
                ref_type="job",
                ref_id=record.id,
                db=db,
            )
