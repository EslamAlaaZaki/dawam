"""Thinking settings shared by the adapters: effort-derived budgets, and which request shape
a Claude model takes (on the Anthropic API or through Bedrock).

Claude 4.6 and newer (Opus/Sonnet 4.6+, Haiku 5+, Fable, Mythos) take adaptive thinking,
``thinking: {"type": "adaptive"}``, with the effort in ``output_config``; a ``budget_tokens``
there is deprecated (4.6) or rejected (4.7 and newer). Older models (Haiku 4.5, Sonnet 4.5, the
Claude 3 family) take ``{"type": "enabled", "budget_tokens": N}`` and no effort. Some new models
(Opus 5.5, Sonnet 5.5) refuse ``{"type": "disabled"}``, so no adapter ever sends it: thinking
is switched off by leaving the field out.

Claude's thinking blocks are sent back unchanged with the rest of a run (preserved thinking):
each carries a ``signature`` bound to the conversation before it, so the run's history stays
append-only, and ``block_binding`` drops (rather than refuses) a block whose prefix changed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from ..gateway import Message, ReasoningSettings

MIN_THINKING_BUDGET = 1024
"""The least thinking budget Anthropic (and Gemini 2.5's useful range) accepts."""
EFFORT_BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}
DEFAULT_THINKING_EFFORT = "medium"
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
"""The beta that ``thinking.block_binding`` needs (a header on the Anthropic API, the
``anthropic_beta`` body field on Bedrock); without it the field is refused."""
CLAUDE_THINKING_TYPES = ("thinking", "redacted_thinking")

_CLAUDE = re.compile(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2})(?!\d))?")
_LEGACY_CLAUDE = re.compile(r"claude-(\d|instant)")


def thinking_budget(reasoning: ReasoningSettings) -> int:
    """The stored budget (at least the minimum), else the effort's, else the default's."""
    if reasoning.budget_tokens:
        return max(MIN_THINKING_BUDGET, reasoning.budget_tokens)
    return EFFORT_BUDGETS.get(reasoning.effort or "", EFFORT_BUDGETS[DEFAULT_THINKING_EFFORT])


def is_claude(model: str) -> bool:
    return "claude" in model.lower()


def claude_adaptive(model: str) -> bool | None:
    """Whether a Claude model id takes adaptive thinking (``True``) or a budget (``False``);
    ``None``: the id names no Claude model this knows (a proxy alias, say). Matches inside
    Bedrock ids too (``us.anthropic.claude-sonnet-4-5-20250929-v1:0``)."""
    name = model.lower()
    match = _CLAUDE.search(name)
    if match:
        family, major, minor = match.group(1), int(match.group(2)), int(match.group(3) or 0)
        if family in ("fable", "mythos"):
            return True
        if family == "haiku":
            return major >= 5
        return (major, minor) >= (4, 6)
    if _LEGACY_CLAUDE.search(name):
        return False
    return None


def adaptive_from_capabilities(capabilities: Any) -> bool | None:
    """Read the Models API ``capabilities`` tree: ``thinking.types.adaptive`` / ``.enabled``."""
    thinking = capabilities.get("thinking") if isinstance(capabilities, dict) else None
    types = thinking.get("types") if isinstance(thinking, dict) else None
    if not isinstance(types, dict):
        return None
    if _supported(types.get("adaptive")):
        return True
    if _supported(types.get("enabled")):
        return False
    return None


def thinks_from_capabilities(capabilities: Any) -> bool | None:
    """``capabilities.thinking.supported`` of the Models API, if it says."""
    thinking = capabilities.get("thinking") if isinstance(capabilities, dict) else None
    value = thinking.get("supported") if isinstance(thinking, dict) else None
    return value if isinstance(value, bool) else None


def _supported(node: Any) -> bool:
    return isinstance(node, dict) and node.get("supported") is True


def claude_thinking_fields(
    reasoning: ReasoningSettings, *, adaptive: bool, think: bool = True
) -> tuple[dict[str, Any], int]:
    """The request fields for Claude thinking (``thinking`` and ``output_config.effort``) and
    the extra output tokens to reserve for it. Thinking is asked for on every step of a run:
    the thinking blocks of earlier steps are sent back with it (``Message.thinking``). The
    ``block_binding`` makes a block whose conversation changed before it a dropped block
    rather than a failed request; it needs the ``THINKING_BINDING_BETA``. ``think`` off (see
    ``bare_tool_loop``): no ``thinking`` field, so no binding either; the effort stays."""
    budget = thinking_budget(reasoning)
    fields: dict[str, Any] = {}
    if adaptive and reasoning.effort:
        fields["output_config"] = {"effort": reasoning.effort}
    if not think:
        return fields, budget if adaptive else 0
    if adaptive:
        thinking: dict[str, Any] = {"type": "adaptive"}
        if reasoning.show:
            thinking["display"] = "summarized"  # 4.7 and newer omit the text by default
    else:
        thinking = {"type": "enabled", "budget_tokens": budget}
    thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
    fields["thinking"] = thinking
    return fields, budget


def bare_tool_loop(messages: Sequence[Message], is_block: Callable[[Any], bool]) -> bool:
    """Whether the current tool loop (the assistant and tool messages since the last user
    message) has an assistant turn with tool calls but none of this provider's thinking blocks
    (``is_block``): a checkpoint saved before blocks were kept, or a step another provider's
    model answered. With thinking on, Claude refuses such a turn, so it goes without."""
    for message in reversed(messages):
        if message.role == "user":
            return False
        if (
            message.role == "assistant"
            and message.tool_calls
            and not any(is_block(b) for b in message.thinking)
        ):
            return True
    return False


def is_claude_thinking_block(block: Any) -> bool:
    """A thinking block the Anthropic Messages API wrote (and reads back)."""
    return isinstance(block, dict) and block.get("type") in CLAUDE_THINKING_TYPES


def transformations(raw: Any) -> tuple[dict[str, Any], ...]:
    """The well-formed entries of an ``input_transformations`` array."""
    if not isinstance(raw, list):
        return ()
    return tuple(dict(entry) for entry in raw if isinstance(entry, dict))
