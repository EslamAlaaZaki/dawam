"""``/api/v1/workspaces/{workspace_id}/assistant``: conversations with the assistant and
the Server-Sent Events stream of its answers (spec §6.16, stories 141, 144, 151, 152).

Handlers only translate HTTP to ``AssistantService`` calls; the service authorizes every
call through the workspaces module's policy, so no handler looks at roles.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.modules.changesets import ChangeSetService
from dawam.modules.files import FileService
from dawam.modules.kpis import KpiService
from dawam.modules.llm import ProviderService, RoleService, WorkspaceAiService
from dawam.modules.notifications import NotificationService
from dawam.modules.sources import (
    PiiService,
    ProfilingService,
    SnapshotService,
    SourceSystemService,
)
from dawam.modules.warehouse import DataWarehouseService, ScoreService
from dawam.modules.workspaces import WorkspaceService

from .internal.agent import Text, ToolFinished
from .internal.tools import ToolRegistry, ToolServices
from .service import (
    AssistantService,
    ChatMessage,
    Completed,
    Conversation,
    ConversationDetail,
    PageContext,
    RunView,
    Started,
    StreamEvent,
    readable_conversations,
)
from .tables import MESSAGE_MAX_LENGTH, TITLE_MAX_LENGTH

router = APIRouter(prefix="/workspaces/{workspace_id}/assistant", tags=["assistant"])


def assistant_service(request: Request) -> AssistantService:
    state = request.app.state
    clock = state.services.clock
    engine = state.engine
    workspaces = WorkspaceService(engine, clock=clock)
    systems = SourceSystemService(engine, workspaces=workspaces, clock=clock)
    providers = ProviderService(
        engine,
        encryption_key=state.settings.encryption_key.get_secret_value(),
        clock=clock,
        **({"adapters": state.services.llm_adapters} if state.services.llm_adapters else {}),
    )
    encryption_key = state.settings.encryption_key.get_secret_value()
    tools = ToolRegistry(
        workspaces,
        ToolServices(
            kpis=KpiService(engine, workspaces=workspaces, systems=systems, clock=clock),
            change_sets=ChangeSetService(
                engine,
                workspaces=workspaces,
                handlers=state.change_set_handlers,
                notifications=NotificationService(engine, clock=clock),
                clock=clock,
                conversations=readable_conversations(engine),
            ),
            files=FileService(
                engine,
                workspaces=workspaces,
                systems=systems,
                warehouses=DataWarehouseService(engine, workspaces=workspaces, clock=clock),
                storage=state.storage,
                search=state.document_search,
                clock=clock,
                max_upload_bytes=state.settings.upload_max_bytes,
            ),
            snapshots=SnapshotService(
                engine,
                workspaces=workspaces,
                jobs=state.services.jobs,
                encryption_key=encryption_key,
                clock=clock,
            ),
            profiling=ProfilingService(
                engine,
                workspaces=workspaces,
                jobs=state.services.jobs,
                encryption_key=encryption_key,
                clock=clock,
            ),
            pii=PiiService(engine, workspaces=workspaces, clock=clock),
            documents=state.document_search,
            scores=ScoreService(engine, workspaces=workspaces, clock=clock),
        ),
    )
    return AssistantService(
        engine,
        workspaces=workspaces,
        roles=RoleService(engine, providers=providers, clock=clock),
        ai=WorkspaceAiService(engine, workspaces=workspaces, clock=clock),
        tools=tools,
        clock=clock,
        max_tool_calls=state.settings.assistant_max_tool_calls,
    )


AssistantServiceDep = Annotated[AssistantService, Depends(assistant_service)]


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    shared_with_workspace: bool = Field(
        description="Every member of the Workspace may read it; only its owner may post."
    )
    mine: bool = Field(description="You started it.")
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationOut]


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    status: str = Field(description="`ok`, `refused`, `error` or `skipped` (over the cap).")
    duration_ms: int


class RunOut(BaseModel):
    id: uuid.UUID
    status: str = Field(
        description="`running`, `completed`, `tool_limit` (the cap was reached), `cancelled` "
        "or `failed`."
    )
    tool_calls: list[ToolCallOut]
    input_tokens: int
    output_tokens: int
    duration_ms: int
    error_code: str | None = Field(description="Why the run failed, e.g. `token_budget_exhausted`.")
    error_message: str | None = Field(description="The reason to show the member.")


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    run: RunOut | None = Field(description="On a member's message: the run that answered it.")


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class CreateConversationRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, max_length=TITLE_MAX_LENGTH)] = (
        Field(default="", description="Optional; the first message names it otherwise.")
    )


class UpdateConversationRequest(BaseModel):
    title: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=TITLE_MAX_LENGTH)
    ] = None
    shared_with_workspace: bool | None = Field(
        default=None, description="Share with, or stop sharing with, the Workspace's members."
    )


class PageContextIn(BaseModel):
    type: Annotated[str, StringConstraints(min_length=1, max_length=40)] = Field(
        description="What the page shows, e.g. `kpi`."
    )
    id: Annotated[str, StringConstraints(max_length=64)] | None = None
    label: Annotated[str, StringConstraints(max_length=200)] | None = Field(
        default=None, description="The object's name, as the page shows it."
    )


class SendMessageRequest(BaseModel):
    content: Annotated[str, StringConstraints(max_length=MESSAGE_MAX_LENGTH * 2)] = Field(
        description=f"The member's message, 1 to {MESSAGE_MAX_LENGTH} characters."
    )
    context: PageContextIn | None = Field(
        default=None, description="The object on the current page; everything else is fetched."
    )


def _conversation(view: Conversation) -> ConversationOut:
    return ConversationOut(
        id=view.id,
        title=view.title,
        shared_with_workspace=view.shared_with_workspace,
        mine=view.mine,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _run(view: RunView) -> RunOut:
    return RunOut(
        id=view.id,
        status=view.status,
        tool_calls=[
            ToolCallOut(
                name=c.name, arguments=c.arguments, status=c.status, duration_ms=c.duration_ms
            )
            for c in view.tool_calls
        ],
        input_tokens=view.input_tokens,
        output_tokens=view.output_tokens,
        duration_ms=view.duration_ms,
        error_code=view.error_code,
        error_message=view.error_message,
    )


def _message(view: ChatMessage) -> MessageOut:
    return MessageOut(
        id=view.id,
        role=view.role,
        content=view.content,
        created_at=view.created_at,
        run=_run(view.run) if view.run else None,
    )


def _detail(view: ConversationDetail) -> ConversationDetailOut:
    return ConversationDetailOut(
        conversation=_conversation(view.conversation),
        messages=[_message(m) for m in view.messages],
    )


@router.get("/conversations", operation_id="listAssistantConversations")
def list_conversations(
    workspace_id: uuid.UUID, user: CurrentUser, service: AssistantServiceDep
) -> ConversationList:
    """Your conversations in the Workspace and those members shared, newest first (any
    member; read-only once the Workspace is archived)."""
    return ConversationList(
        items=[_conversation(c) for c in service.list_conversations(user, workspace_id)]
    )


@router.post("/conversations", status_code=201, operation_id="createAssistantConversation")
def create_conversation(
    workspace_id: uuid.UUID,
    body: CreateConversationRequest,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> ConversationOut:
    """Start a conversation, private to you. 409 `workspace_archived`."""
    return _conversation(service.create_conversation(user, workspace_id, title=body.title))


@router.get("/conversations/{conversation_id}", operation_id="getAssistantConversation")
def get_conversation(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> ConversationDetailOut:
    """A conversation with its messages, and per answer the tools used, tokens and duration.
    404 for somebody else's private conversation."""
    return _detail(service.get_conversation(user, workspace_id, conversation_id))


@router.patch("/conversations/{conversation_id}", operation_id="updateAssistantConversation")
def update_conversation(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: UpdateConversationRequest,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> ConversationOut:
    """Rename a conversation or share it with the Workspace's members. 403
    `not_your_conversation` for a shared conversation somebody else started."""
    return _conversation(
        service.update_conversation(
            user,
            workspace_id,
            conversation_id,
            title=body.title,
            shared_with_workspace=body.shared_with_workspace,
        )
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _frames(events: Iterator[StreamEvent]) -> Iterator[str]:
    for event in events:
        if isinstance(event, Started):
            yield _sse(
                "started", {"run_id": str(event.run_id), "message_id": str(event.message_id)}
            )
        elif isinstance(event, Text):
            yield _sse("text", {"text": event.text})
        elif isinstance(event, ToolFinished):
            call = event.call
            yield _sse(
                "tool",
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": call.status,
                    "duration_ms": call.duration_ms,
                },
            )
        elif isinstance(event, Completed):
            yield _sse(
                "done",
                {
                    "run": json.loads(_run(event.run).model_dump_json()),
                    "message_id": str(event.message_id) if event.message_id else None,
                },
            )


@router.post(
    "/conversations/{conversation_id}/messages",
    operation_id="sendAssistantMessage",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "A Server-Sent Events stream: `started` (run and message ids), "
            "`text` (an answer fragment), `tool` (a finished tool call: name, arguments, "
            "status, duration), then one `done` with the run (status, tools, tokens, "
            "duration and, when the run could not finish, `error_code` and `error_message`).",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
def send_message(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> StreamingResponse:
    """Ask the assistant and stream its answer. Refusals before the stream starts are
    ordinary errors (409 `workspace_archived`, 409 `run_in_progress`, 403
    `not_your_conversation`, 422 `invalid_message`); a model that cannot answer (provider
    down, budget spent, no model assigned) ends the stream with `done` carrying the reason."""
    context = (
        PageContext(body.context.type, body.context.id, body.context.label)
        if body.context
        else None
    )
    events = service.send_message(user, workspace_id, conversation_id, body.content, context)
    return StreamingResponse(
        _frames(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/conversations/{conversation_id}/stop",
    status_code=204,
    operation_id="stopAssistantResponse",
)
def stop_response(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> Response:
    """Stop the conversation's running response; what was written so far is kept."""
    service.stop(user, workspace_id, conversation_id)
    return Response(status_code=204)
