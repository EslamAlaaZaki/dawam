"""Assistant module: the chat panel's backend and the agent loop (spec §6.16, Epic Q).

Public interface. Other modules import only what is re-exported here:

- ``AssistantService``: a member's conversations in a Workspace (private to them unless
  shared with the Workspace's members; read-only in an archived Workspace) and
  ``send_message``, which runs the tool-use loop and returns the stream of its events
  (``Started``, ``Text``, ``ToolFinished``, ``Completed``). The loop depends only on the
  ``llm`` module's gateway interface: every model call goes through
  ``RoleService.gateway_for_role``, so budgets and the internal-only restriction apply.
- The tool registry (``ToolRegistry``): every tool call is authorized through the
  workspaces module's ``can`` as the requesting user (a viewer's chat can only use read
  tools) and respects the Workspace's data-sharing level. Tool results, page context and
  everything read from the Workspace reach the model as quoted data.
- Long tasks as jobs (story 153): the ``start_job`` tool hands a task to an ``assistant_task``
  background job (``ASSISTANT_JOB``), run by ``AssistantService.run_job`` with a higher tool-call
  cap; it logs progress, ends in at most one Change Set, and reports into the conversation.
  ``build_assistant_service`` assembles the service for both the API and the worker.
- ``router``: ``GET|POST /workspaces/{workspace_id}/assistant/conversations``,
  ``GET|PATCH .../conversations/{conversation_id}``, ``POST .../conversations/{id}/messages``
  (answers over Server-Sent Events), ``POST .../conversations/{id}/stop``,
  ``PUT .../conversations/{id}/runs/{run_id}/feedback`` (a member's up/down rating and
  comment on an answer) and, for Owners and admins, ``GET .../feedback`` and
  ``GET .../feedback/eval-candidates``; for installation admins, the run explorer:
  ``GET /admin/assistant/runs`` and ``GET /admin/assistant/runs/{run_id}`` (steps, redacted
  prompts, tokens, cost, prompt version and feedback).

Owns the ``assistant_conversations``, ``assistant_messages``, ``assistant_runs`` and
``assistant_run_feedback`` tables.
Imports ``auth``, ``workspaces``, ``jobs``, ``llm``, ``files``, ``kpis``, ``changesets``,
``notifications``, ``sources`` and ``warehouse``.
"""

from fastapi import APIRouter

from .admin_api import router as admin_router
from .api import router as chat_router
from .assembly import build_assistant_service
from .internal.agent import DEFAULT_MAX_JOB_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS
from .internal.tools import ASSISTANT_JOB, ToolRegistry, ToolServices
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
    Sender,
    Started,
    ToolCallView,
    readable_conversations,
)

router = APIRouter()
router.include_router(chat_router)
router.include_router(admin_router)

__all__ = [
    "ASSISTANT_JOB",
    "DEFAULT_MAX_JOB_TOOL_CALLS",
    "DEFAULT_MAX_TOOL_CALLS",
    "AssistantService",
    "ChatMessage",
    "Completed",
    "Conversation",
    "ConversationDetail",
    "FeedbackItem",
    "FeedbackView",
    "PageContext",
    "RunView",
    "Sender",
    "Started",
    "ToolCallView",
    "ToolRegistry",
    "ToolServices",
    "build_assistant_service",
    "readable_conversations",
    "router",
]
