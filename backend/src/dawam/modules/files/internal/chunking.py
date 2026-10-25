"""Cutting a document's text into passages that can be cited.

A passage belongs to a *section*: the nearest Markdown heading above it, otherwise
"Part N". Long sections are split on paragraph breaks into passages of about
``TARGET_TOKENS`` tokens, never more than ``MAX_TOKENS``. Size is measured in tokens of
the Workspace's embedding model (``count``); consecutive passages of a section share
about ``OVERLAP_TOKENS`` tokens, so a sentence cut at a boundary is whole in one of them.

Three strategies decide the pieces a section is packed from: ``headings`` (its paragraphs),
``recursive`` (paragraphs, then lines, sentences and words, cutting only what is too long) and
``semantic`` (sentences, cut where the embedding similarity of neighbours drops). Whatever the
strategy, the size limits hold.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

# Code constants until Document AI settings (24.1) make them admin settings. The numbers
# equal the former 1,000 / 1,500 characters at about four characters a token.
TARGET_TOKENS = 250
MAX_TOKENS = 375
OVERLAP_TOKENS = 40
SECTION_MAX_CHARS = 200
SEMANTIC_BREAK_SIMILARITY = 0.5
"""Neighbouring sentences less similar than this start a new passage."""
DEFAULT_STRATEGY = "headings"
STRATEGIES = ("headings", "recursive", "semantic")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_SENTENCE = re.compile(r"(?<=[.!?؟])\s+|\n+")
_SEPARATORS = (r"\n\s*\n", r"\n", r"(?<=[.!?؟])\s+")  # then words, in ``_split_long``

Strategy = Literal["headings", "recursive", "semantic"]
TokenCounter = Callable[[str], int]
Embedder = Callable[[list[str]], list[list[float]]]


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


def _recursive_units(text: str, limit: int, count: TokenCounter, level: int = 0) -> list[str]:
    """``text`` cut at the coarsest separator that leaves pieces of at most ``limit``
    tokens: paragraphs, then lines, then sentences, then (``_split_long``) words."""
    text = text.strip()
    if not text:
        return []
    if count(text) <= limit:
        return [text]
    if level >= len(_SEPARATORS):
        return _split_long(text, limit, count)
    parts = [p for p in re.split(_SEPARATORS[level], text) if p.strip()]
    if len(parts) < 2:
        return _recursive_units(text, limit, count, level + 1)
    return [u for part in parts for u in _recursive_units(part, limit, count, level + 1)]


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=False)) / norm if norm else 0.0


def _semantic_blocks(paragraphs: list[str], embed: Embedder) -> list[list[str]] | None:
    """The sentences of ``paragraphs`` in blocks, a new block where the embedding similarity
    to the previous sentence drops below ``SEMANTIC_BREAK_SIMILARITY``. ``None``: ``embed``
    returned a different number of vectors than sentences, so the caller falls back."""
    sentences = [s.strip() for p in paragraphs for s in _SENTENCE.split(p) if s.strip()]
    if len(sentences) < 2:
        return [sentences]
    vectors = embed(sentences)
    if len(vectors) != len(sentences):
        return None
    groups = [[sentences[0]]]
    for sentence, before, after in zip(sentences[1:], vectors[:-1], vectors[1:], strict=True):
        if _cosine(before, after) < SEMANTIC_BREAK_SIMILARITY:
            groups.append([])
        groups[-1].append(sentence)
    return [[" ".join(g)] for g in groups]


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
    strategy: Strategy = DEFAULT_STRATEGY,
    embed: Embedder | None = None,
) -> list[Chunk]:
    """The passages of ``text``, in reading order. Empty for blank text. ``count`` measures
    tokens in the embedding model's own tokenizer. ``semantic`` needs ``embed`` (texts to
    vectors) and otherwise behaves as ``recursive``."""
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
        limit = maximum - overlap
        blocks = None
        if strategy == "semantic" and embed is not None:
            blocks = _semantic_blocks(paragraphs, embed)  # a topic change ends a passage
        if blocks is not None:
            pass
        elif strategy in ("recursive", "semantic"):
            blocks = [[u for p in paragraphs for u in _recursive_units(p, target, count)]]
        else:
            blocks = [paragraphs]
        for block in blocks:
            current = ""
            current_tokens = 0
            fresh = True  # ``current`` holds at most the overlap of the previous passage
            for paragraph in (s for p in block for s in _split_long(p, limit, count)):
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
