"""Thinking settings shared by the adapters: effort-derived budgets, and which request shape
a Claude model takes (on the Anthropic API or through Bedrock).

Claude 4.6 and newer (Opus/Sonnet 4.6+, Haiku 5+, Fable, Mythos) take adaptive thinking,
``thinking: {"type": "adaptive"}``, with the effort in ``output_config``; a ``budget_tokens``
there is deprecated (4.6) or rejected (4.7 and newer). Older models (Haiku 4.5, Sonnet 4.5, the
Claude 3 family) take ``{"type": "enabled", "budget_tokens": N}`` and no effort. Some new models
(Opus 5.5, Sonnet 5.5) refuse ``{"type": "disabled"}``, so no adapter ever sends it: thinking
is switched off by leaving the field out.
"""

from __future__ import annotations

import re
from typing import Any

from ..gateway import ReasoningSettings

MIN_THINKING_BUDGET = 1024
"""The least thinking budget Anthropic (and Gemini 2.5's useful range) accepts."""
EFFORT_BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}
DEFAULT_THINKING_EFFORT = "medium"

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
    reasoning: ReasoningSettings, *, adaptive: bool, think: bool
) -> tuple[dict[str, Any], int]:
    """The request fields for Claude thinking (``thinking`` and ``output_config.effort``) and
    the extra output tokens to reserve for it. ``think`` off (the run has called a tool, see
    the Anthropic adapter's ``_can_think``): no ``thinking`` field. Adaptive models get the
    effort on every turn all the same (a change mid-run would also reset the prompt cache)."""
    fields: dict[str, Any] = {}
    budget = thinking_budget(reasoning)
    if adaptive:
        if reasoning.effort:
            fields["output_config"] = {"effort": reasoning.effort}
        if think:
            thinking: dict[str, Any] = {"type": "adaptive"}
            if reasoning.show:
                thinking["display"] = "summarized"  # 4.7 and newer omit the text by default
            fields["thinking"] = thinking
        return fields, budget
    if think:
        fields["thinking"] = {"type": "enabled", "budget_tokens": budget}
        return fields, budget
    return fields, 0
