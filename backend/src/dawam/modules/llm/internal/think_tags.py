"""Splits ``<think>...</think>`` out of a reply (Qwen3, DeepSeek-R1 and other servers that
put the model's reasoning inline in the content). Streams arrive in arbitrary pieces, so a
tag may be cut in two: the splitter holds back a possible partial tag until it is known."""

from __future__ import annotations

OPEN, CLOSE = "<think>", "</think>"


class ThinkTagSplitter:
    def __init__(self) -> None:
        self._thinking = False
        self._held = ""

    def feed(self, chunk: str) -> list[tuple[bool, str]]:
        """``(is_reasoning, text)`` pieces for ``chunk``, in order."""
        text = self._held + chunk
        self._held = ""
        pieces: list[tuple[bool, str]] = []
        while text:
            tag = CLOSE if self._thinking else OPEN
            at = text.find(tag)
            if at >= 0:
                if at:
                    pieces.append((self._thinking, text[:at]))
                self._thinking = not self._thinking
                text = text[at + len(tag) :]
                continue
            keep = _partial_tag_length(text, tag)
            if len(text) > keep:
                pieces.append((self._thinking, text[: len(text) - keep]))
            self._held = text[len(text) - keep :]
            break
        return pieces

    def flush(self) -> list[tuple[bool, str]]:
        """What is still held when the reply ends."""
        held, self._held = self._held, ""
        return [(self._thinking, held)] if held else []


def _partial_tag_length(text: str, tag: str) -> int:
    for size in range(min(len(text), len(tag) - 1), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0
