"""LLM module: the one gateway every AI feature goes through (spec §6.18).

Public interface. Other modules import only what is re-exported here:

- ``ProviderService``: the admin's registry of providers and models (``Provider``,
  ``Model``, ``ProviderInput``, ``ModelInput``, ``SetupStatus``). ``gateway_for(model_id)``
  is how any other module reaches a model: it returns a ``Gateway``. API keys are sealed
  with ``SecretBox`` and never returned.
- ``Gateway``: ``chat(messages, tools, stream) -> events``, ``embed(texts) -> vectors``
  and ``capabilities()``. Messages, tool calls, stream events (``TextDelta``,
  ``ToolCallEvent``, ``Done``), ``Usage`` and errors (``LlmError`` with a common code)
  are normalised into one internal format (``Message``, ``ToolCall``, ``ToolSpec``,
  ``Capabilities``); rate limits and unavailability are retried with backoff.
- ``Adapter``: the protocol a provider family implements; ``adapter_for`` builds the
  ``openai_compatible`` one (vLLM, SGLang, Ollama, OpenAI and any server exposing
  ``/chat/completions`` and ``/embeddings``) or the ``anthropic`` one (Claude through
  the Messages API; chat only, no embeddings). Nothing else imports a vendor SDK.
- ``FakeAdapter`` (with ``Reply``): the scripted fake provider every test can use.
- ``RoleService``: model roles (``agent`` required, ``light``, ``embedding``), monthly token
  budgets (installation and per Workspace) and usage. ``gateway_for_role(role, workspace_id=,
  user_id=)`` is how features call a model: the ``MeteredGateway`` checks the budgets before
  every call (429 ``token_budget_exhausted``) and records tokens per call; ``light`` falls
  back to the agent model when unassigned or not allowed.
- ``WorkspaceAiService``: a Workspace's AI settings (its agent model, internal-only, data-sharing
  level; owners only, audited). ``policy(workspace_id)`` is the one ``DataSharingPolicy``
  other modules ask before sending data to a model: ``policy.allows(DataSharingLevel.SAMPLES)``
  (levels are cumulative: metadata < profiles < documents < samples). An internal-only
  Workspace's gateway refuses external providers for every role (409
  ``external_provider_refused``). ``on_workspace_created`` is the ``WorkspaceCreatedHook`` the
  composition root sets (``app.state.on_workspace_created``): internal-only by default when the
  installation's agent model is internal, decided in code.
- ``router``: ``GET|PUT /workspaces/{workspace_id}/ai-settings`` (members read, owners
  change), then, admins only, ``/admin/llm/roles``, ``/admin/llm/budgets[...]``,
  ``/admin/llm/usage``, ``/admin/llm/providers[/{provider_id}[/models]]``,
  ``/admin/llm/models/{model_id}[/test]`` and ``/admin/llm/setup``, admins only.

Owns the ``llm_providers``, ``llm_models``, ``llm_settings``, ``llm_workspace_budgets``,
``llm_usage`` and ``llm_workspace_settings`` tables. Imports ``auth``, ``activity``, ``audit`` and ``workspaces``
(for the admin policy).
"""

from fastapi import APIRouter

from .api import router as admin_router
from .gateway import (
    Adapter,
    Capabilities,
    ChatEvent,
    Done,
    Gateway,
    LlmError,
    Message,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
    Usage,
)
from .internal.adapters import AdapterConfig, AdapterFactory, adapter_for
from .internal.fake import FakeAdapter, Reply
from .role_service import (
    MeteredGateway,
    RoleAssignments,
    RoleService,
    UsageReport,
)
from .service import (
    Model,
    ModelInput,
    Provider,
    ProviderInput,
    ProviderService,
    SetupStatus,
)
from .workspace_ai import (
    ApprovedModel,
    DataSharingLevel,
    DataSharingPolicy,
    WorkspaceAiService,
    WorkspaceAiSettings,
    WorkspaceAiView,
    on_workspace_created,
)
from .workspace_ai_api import router as workspace_ai_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(workspace_ai_router)

__all__ = [
    "Adapter",
    "AdapterConfig",
    "AdapterFactory",
    "ApprovedModel",
    "Capabilities",
    "ChatEvent",
    "DataSharingLevel",
    "DataSharingPolicy",
    "Done",
    "FakeAdapter",
    "Gateway",
    "LlmError",
    "Message",
    "MeteredGateway",
    "Model",
    "ModelInput",
    "Provider",
    "ProviderInput",
    "ProviderService",
    "Reply",
    "RoleAssignments",
    "RoleService",
    "SetupStatus",
    "TextDelta",
    "ToolCall",
    "ToolCallEvent",
    "ToolSpec",
    "Usage",
    "UsageReport",
    "WorkspaceAiService",
    "WorkspaceAiSettings",
    "WorkspaceAiView",
    "adapter_for",
    "on_workspace_created",
    "router",
]
