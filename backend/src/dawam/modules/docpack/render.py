"""A tiny document model and its two renderers: Markdown pages and one HTML file.

Every text is escaped for its target (a Markdown table cell, or HTML), so a description
a user typed can never break a table or inject markup into the single-file HTML.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Bullets:
    items: list[str]


@dataclass(frozen=True)
class Table:
    header: list[str]
    rows: list[list[str]]


Block = Heading | Paragraph | Bullets | Table


@dataclass(frozen=True)
class Page:
    path: str
    """The page's file in the zip, e.g. ``source-schemas/cbs.md``."""
    title: str
    blocks: list[Block] = field(default_factory=list)


def _cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _inline(value: str) -> str:
    return " ".join(value.split())


def markdown(blocks: list[Block], *, shift: int = 0) -> str:
    """The blocks as Markdown; ``shift`` deepens every heading (for a combined file)."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            parts.append(f"{'#' * min(block.level + shift, 6)} {_inline(block.text)}")
        elif isinstance(block, Paragraph):
            parts.append(block.text.strip())
        elif isinstance(block, Bullets):
            parts.append("\n".join(f"- {_inline(i)}" for i in block.items))
        else:
            lines = [
                "| " + " | ".join(_cell(h) for h in block.header) + " |",
                "|" + "|".join(" --- " for _ in block.header) + "|",
                *("| " + " | ".join(_cell(c) for c in row) + " |" for row in block.rows),
            ]
            parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def _html_blocks(blocks: list[Block]) -> str:
    out: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            level = min(block.level, 6)
            out.append(f"<h{level}>{html.escape(_inline(block.text))}</h{level}>")
        elif isinstance(block, Paragraph):
            out.append(f"<p>{html.escape(block.text.strip())}</p>")
        elif isinstance(block, Bullets):
            items = "".join(f"<li>{html.escape(_inline(i))}</li>" for i in block.items)
            out.append(f"<ul>{items}</ul>")
        else:
            head = "".join(f"<th>{html.escape(h)}</th>" for h in block.header)
            body = "".join(
                "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>"
                for row in block.rows
            )
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
    return "\n".join(out)


_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:64rem;margin:2rem auto;padding:0 1rem;"
    "line-height:1.5;color:#1b1f24}table{border-collapse:collapse;margin:1rem 0;width:100%}"
    "th,td{border:1px solid #c9d1d9;padding:.3rem .6rem;text-align:left;vertical-align:top}"
    "th{background:#f0f3f6}nav li{margin:.2rem 0}"
)


def single_html(title: str, pages: list[Page]) -> str:
    """One self-contained HTML file: a table of contents, then every page as a section.
    No script and no external resource."""
    toc = "".join(
        f'<li><a href="#page-{i}">{html.escape(p.title)}</a></li>' for i, p in enumerate(pages)
    )
    sections = "\n".join(
        f'<section id="page-{i}">\n{_html_blocks(p.blocks)}\n</section>'
        for i, p in enumerate(pages)
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        f"<title>{html.escape(title)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{html.escape(title)}</h1>\n<nav><ul>{toc}</ul></nav>\n{sections}\n</body>\n</html>\n"
    )
