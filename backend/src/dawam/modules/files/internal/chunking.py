"""Cutting a document's text into passages that can be cited.

A passage belongs to a *section*: the nearest Markdown heading above it, otherwise
"Part N". Long sections are split on paragraph breaks into passages of about
``TARGET_TOKENS`` tokens, never more than ``MAX_TOKENS``. Size is measured in tokens of
the Workspace's embedding model (``count``); consecutive passages of a section share
about ``OVERLAP_TOKENS`` tokens, so a sentence cut at a boundary is whole in one of them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# Code constants until Document AI settings (24.1) make them admin settings. The numbers
# equal the former 1,000 / 1,500 characters at about four characters a token.
TARGET_TOKENS = 250
MAX_TOKENS = 375
OVERLAP_TOKENS = 40
SECTION_MAX_CHARS = 200
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")

TokenCounter = Callable[[str], int]


def estimate_tokens(text: str) -> int:
    """The fallback counter: about four characters a token."""
    return -(-len(text) // 4)


@dataclass(frozen=True)
class Chunk:
    section: str
    text: str


def _split_long(paragraph: str, limit: int, count: TokenCounter) -> list[str]:
    """``paragraph`` cut, preferably at spaces, into pieces of at most ``limit`` tokens."""
    tokens = count(paragraph)
    if tokens <= limit or len(paragraph) < 2:
        return [paragraph]
    middle = len(paragraph) // 2
    cut = paragraph.rfind(" ", 0, middle + 1)
    cut = cut if cut > 0 else middle
    left, right = paragraph[:cut].strip(), paragraph[cut:].strip()
    return [piece for half in (left, right) if half for piece in _split_long(half, limit, count)]


def _tail(text: str, overlap: int, count: TokenCounter) -> str:
    """The last words of ``text``, at most ``overlap`` tokens."""
    if overlap <= 0:
        return ""
    total = count(text)
    if total <= overlap:
        return text
    start = len(text) - max(1, len(text) * overlap // total)
    words = text[start:].split(" ")
    if start > 0 and not text[start - 1].isspace():
        words = words[1:]  # the first word is cut in half
    while words and count(" ".join(words)) > overlap:
        words = words[1:]
    return " ".join(words)


def chunk(
    text: str,
    count: TokenCounter = estimate_tokens,
    *,
    target: int = TARGET_TOKENS,
    maximum: int = MAX_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[Chunk]:
    """The passages of ``text``, in reading order. Empty for blank text. ``count`` measures
    tokens in the embedding model's own tokenizer."""
    overlap = max(0, min(overlap, maximum // 2))
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            sections.append((heading.group(1)[:SECTION_MAX_CHARS], []))
        else:
            sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for title, lines in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
        if not paragraphs:
            continue
        name = title or f"Part {len(chunks) + 1}"
        current = ""
        current_tokens = 0
        fresh = True  # ``current`` holds at most the overlap of the previous passage
        for paragraph in (s for p in paragraphs for s in _split_long(p, maximum - overlap, count)):
            size = count(paragraph)
            if not fresh and current_tokens + size > target:
                chunks.append(Chunk(name, current))
                current = _tail(current, overlap, count)
                current_tokens = count(current) if current else 0
                fresh = True
            current = f"{current}\n\n{paragraph}" if current else paragraph
            current_tokens += size
            fresh = False
        if not fresh:
            chunks.append(Chunk(name, current))
    return chunks
