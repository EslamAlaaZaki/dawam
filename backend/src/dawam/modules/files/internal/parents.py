"""Small-to-big chunking: small child passages are embedded and matched, and the larger
parent section they came from is what search returns.

A parent is cut the same way as a passage, only bigger (``PARENT_TARGET_TOKENS``), without
overlap; its children are cut from the parent's own text, so a child never straddles two
parents and every child of a parent is part of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from dawam.modules.files.internal.chunking import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    Chunk,
    Embedder,
    Strategy,
    TokenCounter,
    chunk,
    estimate_tokens,
)

PARENT_TARGET_TOKENS = 1000
PARENT_MAX_TOKENS = 1500


@dataclass(frozen=True)
class ChildChunk:
    """A passage to embed and its parent's position and text."""

    chunk: Chunk
    parent: int
    parent_text: str


def chunk_with_parents(
    text: str,
    count: TokenCounter = estimate_tokens,
    *,
    strategy: Strategy = "headings",
    embed: Embedder | None = None,
    target: int = TARGET_TOKENS,
    overlap: int = OVERLAP_TOKENS,
) -> list[ChildChunk]:
    """The child passages of ``text``, in reading order, each with its parent section. The
    Workspace's chunking ``strategy`` cuts the parents; a ``semantic`` parent has already
    ended where the topic changed, so its children are cut by size (``recursive``), each of
    about ``target`` tokens (never more than one and a half times it) sharing ``overlap``."""
    children: list[ChildChunk] = []
    parents = chunk(
        text,
        count,
        target=PARENT_TARGET_TOKENS,
        maximum=PARENT_MAX_TOKENS,
        overlap=0,
        strategy=strategy,
        embed=embed,
    )
    child_strategy: Strategy = "recursive" if strategy == "semantic" else strategy
    for index, parent in enumerate(parents):
        for child in chunk(
            parent.text,
            count,
            target=target,
            maximum=target * 3 // 2,
            overlap=overlap,
            strategy=child_strategy,
        ):
            children.append(
                ChildChunk(Chunk(parent.section, child.text, parent.page), index, parent.text)
            )
    return children
