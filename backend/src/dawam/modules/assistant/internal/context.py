"""Context management: fit a conversation into the model's context window (spec §6.18).

Smaller self-hosted models have small windows. Before each model call, large tool results
are cut (``shorten_result``, applied as results arrive) and, if the conversation is still
too big, the oldest turns are left out, then the oldest tool results of the current run.
The member's latest message and the current run's calls are never dropped. Token counts
are estimates (about four characters per token); with an unknown window nothing is trimmed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from dawam.modules.llm import Message

CHARS_PER_TOKEN = 4
PROMPT_SHARE = 0.75
"""The share of the window the prompt may use; the rest is left for the answer."""
RESULT_SHARE = 1 / 6
"""The share of the window one tool result may use."""

_OMITTED_TURNS = (
    "\n\n(Earlier messages of this conversation were left out to fit the model's context window.)"
)
_TOOL_SOURCE = '<data source="tool:'


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def _tokens(message: Message) -> int:
    calls = "".join(f"{c.name}{c.arguments}" for c in message.tool_calls)
    return estimate_tokens((message.content or "") + calls)


def shorten_result(text: str, window: int | None) -> str:
    """``text`` cut to the share of ``window`` one tool result may use, with a note."""
    if window is None:
        return text
    limit = max(200, int(window * RESULT_SHARE)) * CHARS_PER_TOKEN
    if len(text) <= limit:
        return text
    left = len(text) - limit
    return f"{text[:limit]}\n[... {left} characters left out to fit the model's context window]"


def _is_tool_result(message: Message) -> bool:
    """Only meaningful for messages after the member's latest one, which the agent wrote:
    a tool message, or (limited mode) a user message carrying a quoted result."""
    return message.role == "tool" or (message.content or "").startswith(_TOOL_SOURCE)


def fit_messages(
    messages: Sequence[Message], window: int | None, *, protect_from: int
) -> list[Message]:
    """``messages`` (the system message first) trimmed to fit ``window``.

    ``protect_from`` is the index of the member's latest message: everything before it
    (after the system message) is history that may be dropped, everything from it on is kept.
    """
    result = list(messages)
    if window is None:
        return result
    budget = int(window * PROMPT_SHARE)
    used = sum(_tokens(m) for m in result)
    if used <= budget:
        return result

    # 1. Drop the oldest history, a tool call together with its results.
    drop = 0
    index = 1
    while used > budget and index < protect_from:
        end = index + 1
        if result[index].tool_calls:
            while end < protect_from and result[end].role == "tool":
                end += 1
        used -= sum(_tokens(m) for m in result[index:end])
        index = end
        drop = end - 1
    while 0 < drop < protect_from - 1 and result[drop + 1].role != "user":
        used -= _tokens(result[drop + 1])  # never start on an assistant or tool turn
        drop += 1
    if drop:
        del result[1 : drop + 1]
        protect_from -= drop
        system = result[0]
        result[0] = replace(system, content=(system.content or "") + _OMITTED_TURNS)
        used = sum(_tokens(m) for m in result)

    # 2. Replace the oldest tool results of the current run, keeping the newest.
    placeholder = "[... the result was left out to fit the model's context window]"
    last = max(
        (i for i in range(protect_from + 1, len(result)) if _is_tool_result(result[i])),
        default=-1,
    )
    for i in range(protect_from + 1, last):
        if used <= budget:
            break
        message = result[i]
        if _is_tool_result(message):
            source = (message.content or "")[len(_TOOL_SOURCE) :].split('"', 1)[0]
            content = f'{_TOOL_SOURCE}{source}">\n{placeholder}\n</data>'
            used -= _tokens(message) - estimate_tokens(content)
            result[i] = replace(message, content=content)
    return result
