"""Conversations with the assistant, and running its agent loop (spec §6.16, §4.3).

Every method authorizes through the workspaces module's policy first. A conversation is
private to the member who started it unless they share it with the Workspace's members,
who may then read it (never post to it). In an archived Workspace the chat is read-only.
``send_message`` checks everything and stores the member's message *before* it returns, so
a refusal is an ordinary HTTP error; what it returns is the stream of events of the run.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.auth import User
from dawam.modules.llm import Message, RoleService, WorkspaceAiService
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.clock import Clock
from dawam.platform.errors import ApiError

from .internal.agent import (
    DEFAULT_MAX_TOOL_CALLS,
    Finished,
    Text,
    ToolCallRecord,
    ToolFinished,
    run_agent,
)
from .internal.prompt import SYSTEM_PROMPT, quote_data
from .internal.tools import ToolRegistry
from .tables import (
    ERROR_MAX_LENGTH,
    MESSAGE_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    ConversationRecord,
    MessageRecord,
    RunRecord,
)

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New conversation"
HISTORY_MESSAGES = 30
"""The most recent turns sent to the model with a new message."""
LIST_LIMIT = 100
STOP_POLL_SECONDS = 1.0
"""How often the stop flag is read while text streams."""
MAX_SAVED_ARGUMENT = 500
"""Characters of a text argument kept on the saved run."""
STALE_RUN = timedelta(minutes=15)
"""A run that has shown no end for this long (e.g. the server restarted) no longer blocks
the conversation."""


@dataclass(frozen=True)
class Conversation:
    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    shared_with_workspace: bool
    mine: bool
    """The caller started it; only they may post to it or share it."""
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ToolCallView:
    name: str
    arguments: dict[str, Any]
    status: str
    duration_ms: int


@dataclass(frozen=True)
class RunView:
    id: uuid.UUID
    status: str
    tool_calls: list[ToolCallView]
    input_tokens: int
    output_tokens: int
    duration_ms: int
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True)
class ChatMessage:
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    run: RunView | None
    """On the member's message: the run that answered it."""


@dataclass(frozen=True)
class ConversationDetail:
    conversation: Conversation
    messages: list[ChatMessage]


@dataclass(frozen=True)
class PageContext:
    """The object on the page the member is looking at."""

    type: str
    id: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class Started:
    run_id: uuid.UUID
    message_id: uuid.UUID


@dataclass(frozen=True)
class Completed:
    run: RunView
    message_id: uuid.UUID | None
    """The saved answer; none when there was no text."""


StreamEvent = Started | Text | ToolFinished | Completed


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "That conversation does not exist.")


def _run_view(record: RunRecord) -> RunView:
    return RunView(
        id=record.id,
        status=record.status,
        tool_calls=[
            ToolCallView(c["name"], c["arguments"], c["status"], c["duration_ms"])
            for c in record.tool_calls
        ],
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        duration_ms=record.duration_ms,
        error_code=record.error_code,
        error_message=record.error_message,
    )


def _title_from(content: str) -> str:
    line = content.strip().splitlines()[0] if content.strip() else DEFAULT_TITLE
    return line if len(line) <= 60 else line[:57] + "..."


class AssistantService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        roles: RoleService,
        ai: WorkspaceAiService,
        tools: ToolRegistry,
        clock: Clock,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._roles = roles
        self._ai = ai
        self._tools = tools
        self._clock = clock
        self._max_tool_calls = max_tool_calls
        self._timer = timer

    # -- conversations ---------------------------------------------------------------

    def list_conversations(self, user: User, workspace_id: uuid.UUID) -> list[Conversation]:
        """The member's own conversations and those shared with the Workspace, newest
        first (any member; archived Workspaces too)."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        query = (
            sa.select(ConversationRecord)
            .where(
                ConversationRecord.workspace_id == workspace_id,
                sa.or_(
                    ConversationRecord.user_id == user.id,
                    ConversationRecord.shared_with_workspace.is_(True),
                ),
            )
            .order_by(ConversationRecord.updated_at.desc(), ConversationRecord.id)
            .limit(LIST_LIMIT)
        )
        with Session(self._engine) as db:
            return [self._view(user, r) for r in db.scalars(query)]

    def create_conversation(
        self, user: User, workspace_id: uuid.UUID, *, title: str | None = None
    ) -> Conversation:
        """Start a conversation, private to the member. 409 ``workspace_archived``."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        now = self._clock()
        record = ConversationRecord(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            user_id=user.id,
            title=_clean_title(title) or DEFAULT_TITLE,
            shared_with_workspace=False,
            created_at=now,
            updated_at=now,
        )
        with Session(self._engine) as db, db.begin():
            db.add(record)
            return self._view(user, record)

    def get_conversation(
        self, user: User, workspace_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ConversationDetail:
        """A conversation with its messages and the runs that answered them: the member's
        own, or one shared with the Workspace. 404 for somebody else's private one."""
        self._workspaces.authorize(user, Action.VIEW_WORKSPACE, workspace_id)
        with Session(self._engine) as db:
            record = self._load(db, user, workspace_id, conversation_id)
            messages = list(
                db.scalars(
                    sa.select(MessageRecord)
                    .where(MessageRecord.conversation_id == conversation_id)
                    .order_by(MessageRecord.position)
                )
            )
            runs = {
                r.user_message_id: _run_view(r)
                for r in db.scalars(
                    sa.select(RunRecord).where(RunRecord.conversation_id == conversation_id)
                )
            }
            return ConversationDetail(
                self._view(user, record),
                [
                    ChatMessage(m.id, m.role, m.content, m.created_at, runs.get(m.id))
                    for m in messages
                ],
            )

    def update_conversation(
        self,
        user: User,
        workspace_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        title: str | None = None,
        shared_with_workspace: bool | None = None,
    ) -> Conversation:
        """Rename a conversation or share it with the Workspace's members (or stop
        sharing), by the member who started it."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        with Session(self._engine) as db, db.begin():
            record = self._load(db, user, workspace_id, conversation_id, owner_only=True)
            if title is not None:
                record.title = _clean_title(title) or record.title
            if shared_with_workspace is not None:
                record.shared_with_workspace = shared_with_workspace
            record.updated_at = self._clock()
            return self._view(user, record)

    # -- asking ----------------------------------------------------------------------

    def send_message(
        self,
        user: User,
        workspace_id: uuid.UUID,
        conversation_id: uuid.UUID,
        content: str,
        context: PageContext | None = None,
    ) -> Iterator[StreamEvent]:
        """Store the member's message and return the events of the run answering it.
        409 ``workspace_archived``, 409 ``run_in_progress``, 422 ``invalid_message``."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        content = content.strip()
        if not content or len(content) > MESSAGE_MAX_LENGTH:
            raise ApiError(
                422,
                "invalid_message",
                f"A message must have 1 to {MESSAGE_MAX_LENGTH} characters.",
                {"field": "content"},
            )
        now = self._clock()
        with Session(self._engine) as db, db.begin():
            record = self._load(db, user, workspace_id, conversation_id, owner_only=True, lock=True)
            busy = db.scalar(
                sa.select(sa.func.count())
                .select_from(RunRecord)
                .where(
                    RunRecord.conversation_id == conversation_id,
                    RunRecord.status == "running",
                    RunRecord.started_at > now - STALE_RUN,
                )
            )
            if busy:
                raise ApiError(
                    409,
                    "run_in_progress",
                    "The assistant is still answering the last message. Stop it or wait.",
                )
            position = self._next_position(db, conversation_id)
            message = MessageRecord(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                position=position,
                role="user",
                content=content,
                created_at=now,
            )
            db.add(message)
            db.flush()
            run = RunRecord(
                id=uuid.uuid4(),
                conversation_id=conversation_id,
                user_message_id=message.id,
                status="running",
                cancel_requested=False,
                tool_calls=[],
                input_tokens=0,
                output_tokens=0,
                duration_ms=0,
                started_at=now,
            )
            db.add(run)
            if record.title == DEFAULT_TITLE:
                record.title = _title_from(content)
            record.updated_at = now
            history = self._history(db, conversation_id)
            started = Started(run.id, message.id)
        return self._stream(user, workspace_id, conversation_id, started, history, context)

    def stop(self, user: User, workspace_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
        """Ask the conversation's running response to stop (by the member who started it).
        Nothing running is not an error."""
        self._workspaces.authorize(user, Action.ASK_ASSISTANT, workspace_id)
        with Session(self._engine) as db, db.begin():
            self._load(db, user, workspace_id, conversation_id, owner_only=True)
            db.execute(
                sa.update(RunRecord)
                .where(RunRecord.conversation_id == conversation_id, RunRecord.status == "running")
                .values(cancel_requested=True)
            )

    # -- the run ---------------------------------------------------------------------

    def _stream(
        self,
        user: User,
        workspace_id: uuid.UUID,
        conversation_id: uuid.UUID,
        started: Started,
        history: Sequence[Message],
        context: PageContext | None,
    ) -> Iterator[StreamEvent]:
        began = self._timer()
        text: list[str] = []
        calls: list[ToolCallRecord] = []
        saved = False
        try:
            yield started
            try:
                gateway = self._roles.gateway_for_role(
                    "agent", workspace_id=workspace_id, user_id=user.id
                )
                tools = self._tools.bind(
                    user, workspace_id, self._ai.policy(workspace_id), conversation_id
                )
                for event in run_agent(
                    gateway,
                    tools,
                    system=_system_prompt(context),
                    history=history,
                    max_tool_calls=self._max_tool_calls,
                    is_cancelled=lambda: self._cancel_requested(started.run_id),
                    timer=self._timer,
                    delta_poll_seconds=STOP_POLL_SECONDS,
                ):
                    if isinstance(event, Finished):
                        finished = event
                        break
                    if isinstance(event, Text):
                        text.append(event.text)
                    else:
                        calls.append(event.call)
                    yield event
                else:  # pragma: no cover - run_agent always ends with Finished
                    finished = Finished("completed", "".join(text), calls, 0, 0)
            except ApiError as exc:  # no model assigned, internal-only refusal
                finished = Finished("failed", "".join(text), calls, 0, 0, exc.code, exc.message)
            except Exception:
                logger.exception("assistant run crashed", extra={"run_id": str(started.run_id)})
                finished = Finished(
                    "failed",
                    "".join(text),
                    calls,
                    0,
                    0,
                    "internal_error",
                    "The assistant hit an unexpected error.",
                )
            completed = self._finish(user, workspace_id, started, finished, self._timer() - began)
            saved = True
            yield completed
        finally:
            if not saved:  # the browser went away mid-answer
                gone = Finished("cancelled", "".join(text), calls, 0, 0)
                self._finish(user, workspace_id, started, gone, self._timer() - began)

    def _finish(
        self,
        user: User,
        workspace_id: uuid.UUID,
        started: Started,
        finished: Finished,
        seconds: float,
    ) -> Completed:
        now = self._clock()
        duration_ms = round(seconds * 1000)
        with Session(self._engine) as db, db.begin():
            run = db.get(RunRecord, started.run_id)
            assert run is not None
            answer_id = None
            if finished.text:
                conversation = db.scalars(
                    sa.select(ConversationRecord)
                    .where(ConversationRecord.id == run.conversation_id)
                    .with_for_update()
                ).one()
                answer_id = uuid.uuid4()
                db.add(
                    MessageRecord(
                        id=answer_id,
                        conversation_id=run.conversation_id,
                        position=self._next_position(db, run.conversation_id),
                        role="assistant",
                        content=finished.text,
                        created_at=now,
                    )
                )
                conversation.updated_at = now
            run.status = finished.status
            run.tool_calls = [
                {
                    "name": c.name,
                    "arguments": _shortened(c.arguments),
                    "status": c.status,
                    "duration_ms": c.duration_ms,
                }
                for c in finished.tool_calls
            ]
            run.input_tokens = finished.prompt_tokens
            run.output_tokens = finished.completion_tokens
            run.duration_ms = duration_ms
            run.error_code = finished.error_code
            run.error_message = (finished.error_message or "")[:ERROR_MAX_LENGTH] or None
            run.answer_message_id = answer_id
            run.finished_at = now
            view = _run_view(run)
        logger.info(
            "assistant run",
            extra={
                "run_id": str(started.run_id),
                "workspace_id": str(workspace_id),
                "user_id": str(user.id),
                "status": finished.status,
                "input_tokens": finished.prompt_tokens,
                "output_tokens": finished.completion_tokens,
                "tools": json.dumps([c.name for c in finished.tool_calls]),
                "duration_ms": duration_ms,
            },
        )
        return Completed(view, answer_id)

    def _cancel_requested(self, run_id: uuid.UUID) -> bool:
        with Session(self._engine) as db:
            return bool(
                db.scalar(sa.select(RunRecord.cancel_requested).where(RunRecord.id == run_id))
            )

    # -- internals -------------------------------------------------------------------

    @staticmethod
    def _view(user: User, record: ConversationRecord) -> Conversation:
        return Conversation(
            id=record.id,
            workspace_id=record.workspace_id,
            title=record.title,
            shared_with_workspace=record.shared_with_workspace,
            mine=record.user_id == user.id,
            owner_id=record.user_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _load(
        db: Session,
        user: User,
        workspace_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        owner_only: bool = False,
        lock: bool = False,
    ) -> ConversationRecord:
        query = sa.select(ConversationRecord).where(ConversationRecord.id == conversation_id)
        record = db.scalars(query.with_for_update() if lock else query).first()
        if record is None or record.workspace_id != workspace_id:
            raise _not_found()
        if record.user_id != user.id:
            if not record.shared_with_workspace:
                raise _not_found()  # somebody else's private conversation does not exist
            if owner_only:
                raise ApiError(
                    403,
                    "not_your_conversation",
                    "Only the member who started this conversation may do that.",
                )
        return record

    @staticmethod
    def _next_position(db: Session, conversation_id: uuid.UUID) -> int:
        last = db.scalar(
            sa.select(sa.func.max(MessageRecord.position)).where(
                MessageRecord.conversation_id == conversation_id
            )
        )
        return (last or 0) + 1

    @staticmethod
    def _history(db: Session, conversation_id: uuid.UUID) -> list[Message]:
        recent = db.scalars(
            sa.select(MessageRecord)
            .where(MessageRecord.conversation_id == conversation_id)
            .order_by(MessageRecord.position.desc())
            .limit(HISTORY_MESSAGES)
        )
        return [
            Message("user" if m.role == "user" else "assistant", m.content)
            for m in reversed(list(recent))
        ]


def _shortened(arguments: dict[str, Any]) -> dict[str, Any]:
    """The arguments as saved on the run: long text (a generated file's content) is cut."""
    return {
        key: value[:MAX_SAVED_ARGUMENT] + "..."
        if isinstance(value, str) and len(value) > MAX_SAVED_ARGUMENT
        else value
        for key, value in arguments.items()
    }


def _clean_title(title: str | None) -> str:
    return (title or "").strip()[:TITLE_MAX_LENGTH]


def _system_prompt(context: PageContext | None) -> str:
    if context is None:
        return SYSTEM_PROMPT
    described = {"type": context.type, "id": context.id, "name": context.label}
    page = quote_data("page context", json.dumps(described, ensure_ascii=False))
    return f"{SYSTEM_PROMPT}\n\nThe member is looking at this object right now:\n{page}"
