"""Which model answers a chat turn (ticket #406): a short, plain question goes to the
Workspace's light model, anything that asks the assistant to make or change something
(and anything long or multi-line) to the agent model. A deliberately small heuristic: when
in doubt the agent model answers."""

from __future__ import annotations

import re

MAX_SIMPLE_CHARS = 160

_GENERATION = re.compile(
    r"\b(create|generate|build|write|draft|make|add|change|update|edit|delete|remove|rename|"
    r"import|load|map|model|design|set up|configure|fix|refactor|migrate|plan|compare|"
    r"analy[sz]e|summari[sz]e|explain why)\b",
    re.IGNORECASE,
)


def is_simple_question(text: str) -> bool:
    """A single short line that ends in a question mark and asks for nothing to be made."""
    text = text.strip()
    return (
        0 < len(text) <= MAX_SIMPLE_CHARS
        and "\n" not in text
        and text.endswith("?")
        and _GENERATION.search(text) is None
    )
