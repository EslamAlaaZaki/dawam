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
- ``router``: ``/admin/llm/providers[/{provider_id}[/models]]``,
  ``/admin/llm/models/{model_id}[/test]`` and ``/admin/llm/setup``, admins only.

Owns the ``llm_providers`` and ``llm_models`` tables. Imports ``auth`` and ``workspaces``
(for the admin policy).
"""

from .api import router
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
from .service import (
    Model,
    ModelInput,
    Provider,
    ProviderInput,
    ProviderService,
    SetupStatus,
)

__all__ = [
    "Adapter",
    "AdapterConfig",
    "AdapterFactory",
    "Capabilities",
    "ChatEvent",
    "Done",
    "FakeAdapter",
    "Gateway",
    "LlmError",
    "Message",
    "Model",
    "ModelInput",
    "Provider",
    "ProviderInput",
    "ProviderService",
    "Reply",
    "SetupStatus",
    "TextDelta",
    "ToolCall",
    "ToolCallEvent",
    "ToolSpec",
    "Usage",
    "adapter_for",
    "router",
]
