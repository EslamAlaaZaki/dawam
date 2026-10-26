"""Structured final answers (AI enhancements, Agent capabilities).

An answer the UI renders as cards is asked for through the gateway's ``json_schema``
support. Today that is a KPI suggestion list: after a run that called ``suggest_kpis``, one
more model call restates the answer as JSON under ``KPI_CARDS_SCHEMA``. The reply is
untrusted, so it is validated strictly and trimmed to the fields the cards show; anything
off drops the cards and the Markdown answer stands alone. The cards travel in the answer as
a fenced ``dawam-kpis`` block, which the web app draws.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .json_schema import validate

CARDS_FENCE = "dawam-kpis"
KPI_CARD_TOOLS = frozenset({"suggest_kpis"})
"""Tools whose successful call makes the final answer a KPI suggestion list."""
MAX_CARDS = 20
_BLOCK = re.compile(rf"```{CARDS_FENCE}\n.*?\n```", re.DOTALL)
MAX_REPLY_CHARS = 64 * 1024

_TEXT = {"type": "string", "maxLength": 4000}
KPI_CARDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intro": {"type": "string", "maxLength": 1000},
        "suggestions": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_CARDS,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 200},
                    "rationale": _TEXT,
                    "definition": _TEXT,
                    "unit": {"type": "string", "maxLength": 50},
                },
                "required": ["name", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["intro", "suggestions"],
    "additionalProperties": False,
}

STRUCTURE_REQUEST = (
    "Restate your last answer as JSON for display as cards: a short intro sentence (no list), "
    "then one entry per KPI you suggested, "
    "with its name, the rationale, and its definition and unit when you know them. Reply with "
    "the JSON only."
)


def parse_cards(reply: str) -> tuple[str, list[dict[str, str]]] | None:
    """The intro and cards in a model's JSON ``reply``, or ``None`` when it is not valid."""
    if len(reply) > MAX_REPLY_CHARS:
        return None
    try:
        parsed = json.loads(reply)
    except (ValueError, RecursionError):
        return None
    if validate(parsed, KPI_CARDS_SCHEMA):
        return None
    cards = [
        {k: item[k] for k in ("name", "rationale", "definition", "unit") if item.get(k)}
        for item in parsed["suggestions"]
    ]
    return parsed["intro"].strip(), cards


def cards_block(cards: list[dict[str, str]]) -> str:
    """The fenced block that carries ``cards`` at the end of an answer."""
    return f"```{CARDS_FENCE}\n{json.dumps(cards, ensure_ascii=False)}\n```"


def strip_cards(text: str) -> str:
    """``text`` with each cards block replaced by a one-line note, so history sent to the
    model on later turns does not carry the cards' tokens again."""
    return _BLOCK.sub("[KPI suggestions shown as cards]", text)
