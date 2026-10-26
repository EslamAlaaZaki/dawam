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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from dawam.modules.auth import CurrentUser
from dawam.platform.pii_validators import redact_json

from .assembly import build_assistant_service
from .internal.agent import Text, ToolFinished
from .service import (
    AssistantService,
    ChatMessage,
    Completed,
    Conversation,
    ConversationDetail,
    FeedbackItem,
    FeedbackView,
    PageContext,
    RunView,
    Started,
    StreamEvent,
)
from .tables import FEEDBACK_COMMENT_MAX_LENGTH, MESSAGE_MAX_LENGTH, TITLE_MAX_LENGTH

router = APIRouter(prefix="/workspaces/{workspace_id}/assistant", tags=["assistant"])


def assistant_service(request: Request) -> AssistantService:
    state = request.app.state
    return build_assistant_service(
        state.engine,
        state.settings,
        state.services.clock,
        jobs=state.services.jobs,
        storage=state.storage,
        document_search=state.document_search,
        change_set_handlers=state.change_set_handlers,
        llm_adapters=state.services.llm_adapters,
    )


AssistantServiceDep = Annotated[AssistantService, Depends(assistant_service)]


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    shared_with_workspace: bool = Field(
        description="Every member of the Workspace may read it; its Owners and Editors may "
        "post to it."
    )
    mine: bool = Field(description="You started it: only you may share or unshare it.")
    can_post: bool = Field(
        description="You may post: you started it, or you are an Owner or Editor and it is "
        "shared. Viewers only read."
    )
    created_at: datetime
    updated_at: datetime
    model_id: uuid.UUID | None = Field(
        description="The model picked for the last message, used again until another is "
        "picked; none until one is."
    )


class ConversationList(BaseModel):
    items: list[ConversationOut]


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    status: str = Field(description="`ok`, `refused`, `error` or `skipped` (over the cap).")
    duration_ms: int
    result: dict[str, Any] | None = Field(
        default=None,
        description="What the tool kept of its result. A source query: `columns`, `row_count`, "
        "`duration_ms` and `can_write` (the Connection user could change data: warn), "
        "never a row.",
    )
    flagged: list[str] = Field(
        default_factory=list,
        description="Documents (or the tool) whose result looked like a prompt injection. "
        "The text still reached the model, wrapped with a warning.",
    )


class FeedbackOut(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None
    updated_at: datetime


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
    feedback: FeedbackOut | None = Field(
        default=None, description="Your own rating of the run's answer, if you gave one."
    )


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    comment: Annotated[
        str | None, StringConstraints(strip_whitespace=True, max_length=FEEDBACK_COMMENT_MAX_LENGTH)
    ] = Field(default=None, description="Optional: what was good or wrong about the answer.")


class FeedbackItemOut(BaseModel):
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    member_email: str | None
    rating: Literal["up", "down"]
    comment: str | None
    question: str
    answer: str
    tools: list[str] = Field(description="Names of the tools the run called.")
    created_at: datetime
    updated_at: datetime


class FeedbackList(BaseModel):
    items: list[FeedbackItemOut]


class EvalCaseCandidate(BaseModel):
    id: str = Field(description="`feedback-<run id>`: stable across exports.")
    input: str = Field(description="The question the member asked.")
    observed_answer: str
    expected_tools: list[str] = Field(description="The tools the run called.")
    label: Literal["up", "down"] = Field(
        description="Down: a failure to turn into a regression case; up: a keeper."
    )
    note: str | None = Field(description="The member's comment.")


class EvalCaseCandidates(BaseModel):
    cases: list[EvalCaseCandidate]


class SenderOut(BaseModel):
    id: uuid.UUID
    email: str


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    run: RunOut | None = Field(description="On a member's message: the run that answered it.")
    sender: SenderOut | None = Field(
        description="On a member's message: who sent it. None on the assistant's replies."
    )
    model: str | None = Field(
        description="On a reply: the display name of the model that answered."
    )


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
    model_id: uuid.UUID | None = Field(
        default=None,
        description="The model to answer with: one of the Workspace's agent models "
        "(422 `model_not_allowed` otherwise). Omitted: the "
        "conversation's last pick, else the Workspace's agent model.",
    )


def _conversation(view: Conversation) -> ConversationOut:
    return ConversationOut(
        id=view.id,
        title=view.title,
        shared_with_workspace=view.shared_with_workspace,
        mine=view.mine,
        can_post=view.can_post,
        created_at=view.created_at,
        updated_at=view.updated_at,
        model_id=view.model_id,
    )


def _run(view: RunView) -> RunOut:
    return RunOut(
        id=view.id,
        status=view.status,
        tool_calls=[
            ToolCallOut(
                name=c.name,
                arguments=c.arguments,
                status=c.status,
                duration_ms=c.duration_ms,
                result=c.result,
                flagged=c.flagged,
            )
            for c in view.tool_calls
        ],
        input_tokens=view.input_tokens,
        output_tokens=view.output_tokens,
        duration_ms=view.duration_ms,
        error_code=view.error_code,
        error_message=view.error_message,
        feedback=_feedback(view.feedback) if view.feedback else None,
    )


def _feedback(view: FeedbackView) -> FeedbackOut:
    return FeedbackOut(rating=view.rating, comment=view.comment, updated_at=view.updated_at)


def _feedback_item(view: FeedbackItem) -> FeedbackItemOut:
    return FeedbackItemOut(**view.__dict__)


def _message(view: ChatMessage) -> MessageOut:
    return MessageOut(
        id=view.id,
        role=view.role,
        content=view.content,
        created_at=view.created_at,
        run=_run(view.run) if view.run else None,
        sender=SenderOut(id=view.sender.id, email=view.sender.email) if view.sender else None,
        model=view.model,
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
                    "arguments": redact_json(call.arguments),
                    "status": call.status,
                    "duration_ms": call.duration_ms,
                    "result": call.result,
                    "flagged": list(call.flagged),
                },
            )
        elif isinstance(event, Completed):
            yield _sse(
                "done",
                {
                    "run": json.loads(_run(event.run).model_dump_json()),
                    "message_id": str(event.message_id) if event.message_id else None,
                    "model": event.model,
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
    `read_only_conversation` for a Viewer in a shared conversation, 404 for somebody
    else's private one, 422 `invalid_message`). The run uses the sender's permissions;
    a model that cannot answer (provider
    down, budget spent, no model assigned) ends the stream with `done` carrying the reason."""
    context = (
        PageContext(body.context.type, body.context.id, body.context.label)
        if body.context
        else None
    )
    events = service.send_message(
        user, workspace_id, conversation_id, body.content, context, body.model_id
    )
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


@router.put(
    "/conversations/{conversation_id}/runs/{run_id}/feedback",
    operation_id="rateAssistantAnswer",
)
def rate_answer(
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    body: FeedbackRequest,
    user: CurrentUser,
    service: AssistantServiceDep,
) -> FeedbackOut:
    """Rate an answer up or down, with an optional comment. Rating again replaces your
    rating (one per member and answer). Any member who can read the conversation; 404 for
    an unknown run, 409 `workspace_archived`."""
    return _feedback(
        service.rate_answer(user, workspace_id, conversation_id, run_id, body.rating, body.comment)
    )


@router.get("/feedback", operation_id="listAssistantFeedback")
def list_feedback(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    service: AssistantServiceDep,
    rating: Literal["up", "down"] | None = None,
) -> FeedbackList:
    """Members' ratings of answers with the question, answer and tools, newest first
    (Workspace Owners and admins; 403 otherwise)."""
    return FeedbackList(
        items=[_feedback_item(i) for i in service.list_feedback(user, workspace_id, rating=rating)]
    )


@router.get("/feedback/eval-candidates", operation_id="exportAssistantEvalCandidates")
def export_eval_candidates(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    service: AssistantServiceDep,
    rating: Literal["up", "down"] | None = "down",
) -> EvalCaseCandidates:
    """The rated answers as eval-case candidates to review and add to the eval suite
    (Workspace Owners and admins). Only thumbs-down by default; `rating` picks the other."""
    return EvalCaseCandidates(
        cases=[
            EvalCaseCandidate(
                id=f"feedback-{i.run_id}",
                input=i.question,
                observed_answer=i.answer,
                expected_tools=i.tools,
                label=i.rating,
                note=i.comment,
            )
            for i in service.list_feedback(user, workspace_id, rating=rating)
        ]
    )
