"""Cutting a document's text into passages that can be cited.

A passage belongs to a *section*: the nearest Markdown heading above it, otherwise
"Part N". A PDF's pages are separated by ``PAGE_BREAK`` in the extracted text; a passage
never spans two pages and records the page it is on. Long sections are split on paragraph
breaks into passages of about ``TARGET_CHARS`` characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 1000
MAX_CHARS = 1500
SECTION_MAX_CHARS = 200
PAGE_BREAK = ""
"""Between the pages of a paged document (a PDF) in its extracted text."""
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class Chunk:
    section: str
    text: str
    page: int | None = None
    """The 1-based page, for a paged document; ``None`` otherwise."""


def _split_long(paragraph: str) -> list[str]:
    return [paragraph[i : i + MAX_CHARS] for i in range(0, len(paragraph), MAX_CHARS)]


def chunk(text: str) -> list[Chunk]:
    """The passages of ``text``, in reading order. Empty for blank text."""
    paged = PAGE_BREAK in text
    sections: list[tuple[str | None, list[str], int | None]] = [(None, [], 1 if paged else None)]
    for number, page_text in enumerate(text.split(PAGE_BREAK), start=1):
        page = number if paged else None
        if number > 1:  # a new page continues the section it began in
            sections.append((sections[-1][0], [], page))
        for line in page_text.splitlines():
            heading = _HEADING.match(line)
            if heading:
                sections.append((heading.group(1)[:SECTION_MAX_CHARS], [], page))
            else:
                sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for title, lines, page in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
        if not paragraphs:
            continue
        name = title or f"Part {len(chunks) + 1}"
        current = ""
        for paragraph in (piece for p in paragraphs for piece in _split_long(p)):
            if current and len(current) + len(paragraph) > TARGET_CHARS:
                chunks.append(Chunk(name, current, page))
                current = ""
            current = f"{current}\n\n{paragraph}" if current else paragraph
        if current:
            chunks.append(Chunk(name, current, page))
    return chunks
