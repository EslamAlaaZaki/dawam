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
  (answers over Server-Sent Events) and ``POST .../conversations/{id}/stop``.

Owns the ``assistant_conversations``, ``assistant_messages`` and ``assistant_runs`` tables.
Imports ``auth``, ``workspaces``, ``jobs``, ``llm``, ``files``, ``kpis``, ``changesets``,
``notifications``, ``sources`` and ``warehouse``.
"""

from .api import router
from .assembly import build_assistant_service
from .internal.agent import DEFAULT_MAX_JOB_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS
from .internal.tools import ASSISTANT_JOB, ToolRegistry, ToolServices
from .service import (
    AssistantService,
    ChatMessage,
    Completed,
    Conversation,
    ConversationDetail,
    PageContext,
    RunView,
    Started,
    ToolCallView,
    readable_conversations,
)

__all__ = [
    "ASSISTANT_JOB",
    "DEFAULT_MAX_JOB_TOOL_CALLS",
    "DEFAULT_MAX_TOOL_CALLS",
    "AssistantService",
    "ChatMessage",
    "Completed",
    "Conversation",
    "ConversationDetail",
    "PageContext",
    "RunView",
    "Started",
    "ToolCallView",
    "ToolRegistry",
    "ToolServices",
    "build_assistant_service",
    "readable_conversations",
    "router",
]
