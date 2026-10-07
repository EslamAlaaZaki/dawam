"""Prompted tool calls for models without native tool calling (spec §6.18, limited mode).

The tools are described in the system prompt and the model asks for one by replying with
nothing but ``{"tool": "<name>", "arguments": {...}}``. The model's text is untrusted, so
parsing is strict: the whole reply (optionally in one code fence) must be exactly one such
object, the tool must be one that was offered, and the arguments must satisfy the tool's
JSON Schema. A reply that does not start like a JSON object is an answer, never a call;
JSON buried in prose is not extracted. Whether the call is *allowed* is still decided
server-side when it runs, exactly as for native calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dawam.modules.llm import ToolSpec

from .json_schema import validate

MAX_REPLY_CHARS = 64 * 1024
"""A longer reply is never parsed as a call (deeply nested JSON can exhaust the stack)."""
MAX_RETRIES = 2
"""How many times an invalid call is sent back to the model before the run fails."""

_FENCE = re.compile(r"\A```[A-Za-z]*[ \t]*\r?\n(.*?)\r?\n?```\Z", re.DOTALL)
_MAX_REASONS = 5


@dataclass(frozen=True)
class PromptedCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class PromptedInvalid:
    """A reply that tried to be a tool call and was not a valid one. ``reason`` is safe to
    send back to the model: it never repeats the model's own values."""

    reason: str


def tools_prompt(specs: Sequence[ToolSpec]) -> str:
    """The system-prompt section that describes the tools and the reply format."""
    lines = [
        "You have these tools. To use one, reply with ONLY a JSON object of the form "
        '{"tool": "<tool name>", "arguments": {...}} and nothing else: no explanation, '
        "no other text, one tool call per reply. Its result will be given to you as quoted "
        "data, and then you may call another tool or answer. To answer the member, reply in "
        "plain text without any JSON object.",
        "",
        "Tools:",
    ]
    for spec in specs:
        lines.append(f"- {spec.name}: {spec.description}")
        lines.append(
            f"  arguments (JSON Schema): {json.dumps(spec.parameters, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def parse_reply(text: str, tools: Mapping[str, ToolSpec]) -> PromptedCall | PromptedInvalid | None:
    """``None`` when ``text`` is an answer, else the call or why it is not a valid one."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1).strip()
    if not body.startswith("{"):
        return None
    if len(body) > MAX_REPLY_CHARS:
        return PromptedInvalid("the reply is too long for a tool call")
    try:
        parsed, end = json.JSONDecoder().raw_decode(body)
    except (ValueError, RecursionError):
        return PromptedInvalid("the JSON could not be parsed")
    if body[end:].strip():
        return PromptedInvalid("there is text after the JSON object")
    if not isinstance(parsed, dict) or set(parsed) != {"tool", "arguments"}:
        return PromptedInvalid('the object must have exactly the keys "tool" and "arguments"')
    name, arguments = parsed["tool"], parsed["arguments"]
    if not isinstance(name, str) or name not in tools:
        return PromptedInvalid(f"unknown tool; the tools are: {', '.join(sorted(tools))}")
    if not isinstance(arguments, dict):
        return PromptedInvalid('"arguments" must be a JSON object')
    errors = validate(arguments, tools[name].parameters)
    if errors:
        return PromptedInvalid("; ".join(errors[:_MAX_REASONS]))
    return PromptedCall(name, arguments)


def correction(reason: str) -> str:
    return (
        f"That was not a valid tool call: {reason}. Reply again with ONLY the corrected "
        "JSON object, or answer in plain text if you do not need a tool."
    )
