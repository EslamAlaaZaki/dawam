"""Mermaid diagrams the ``render_diagram`` tool builds from the DW model and the lineage graph.

The diagram is built here from what DAWAM holds, never from what the model remembers: the
model only picks the kind and scope, then repeats the text in a ``mermaid`` code block that
the chat draws. Object names are untrusted text, so they are reduced to identifier characters
(or, in a label, stripped of quotes and markup) before they reach the diagram source.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

MAX_TABLES = 30
MAX_COLUMNS = 15
MAX_LINEAGE_NODES = 60

_UNSAFE = re.compile(r"[^A-Za-z0-9_]")
_LABEL_UNSAFE = re.compile(r"[\"<>`\\\r\n{}\[\]()|;#&]")


def ident(name: str) -> str:
    """A name as a Mermaid identifier (letters, digits and underscores)."""
    return _UNSAFE.sub("_", name) or "_"


def label(text: str) -> str:
    """Text safe inside a double-quoted Mermaid label."""
    return _LABEL_UNSAFE.sub(" ", text).strip()[:80]


def star_schema(tables: Sequence[Any], scope: str | None) -> tuple[str, int]:
    """An ER diagram of fact tables and the dimensions their foreign keys point at.

    ``tables`` are the DW schema's tables; ``scope`` is a Layer, a table name or a table id
    (a dimension shows the facts that use it); ``None`` is every fact table. Returns the
    diagram and how many tables were left out by the size limit; an empty diagram text means
    nothing matched.
    """
    by_id = {t.id: t for t in tables}
    wanted = (scope or "").strip().casefold()
    if not wanted:
        roots = [t for t in tables if t.kind == "fact"]
    elif wanted in ("core", "mart", "staging"):
        roots = [t for t in tables if t.layer == wanted and t.kind == "fact"]
    else:
        named = [t for t in tables if t.name.casefold() == wanted or str(t.id) == wanted]
        roots = []
        for table in named:
            if table.kind == "fact":
                roots.append(table)
            else:  # a dimension: the facts that point at it
                roots += [
                    t
                    for t in tables
                    if t.kind == "fact" and any(c.references_table_id == table.id for c in t.columns)
                ]
    if not roots:
        return "", 0
    shown = roots[:MAX_TABLES]
    left_out = len(roots) - len(shown)
    members = {t.id: t for t in shown}
    links: list[tuple[Any, Any, Any]] = []
    for fact in shown:
        for column in fact.columns:
            dim = by_id.get(column.references_table_id) if column.references_table_id else None
            if dim is not None:
                members.setdefault(dim.id, dim)
                links.append((fact, dim, column))
    lines = ["erDiagram"]
    for fact, dim, column in links:
        lines.append(
            f"    {ident(fact.name)} }}o--|| {ident(dim.name)} : \"{label(column.name)}\""
        )
    for table in members.values():
        lines.append(f"    {ident(table.name)} {{")
        for column in table.columns[:MAX_COLUMNS]:
            key = {"sk": " PK", "fk": " FK"}.get(column.role, "")
            lines.append(f"        {ident(column.role)} {ident(column.name)}{key}")
        lines.append("    }")
    return "\n".join(lines), left_out


def lineage(graph: Any) -> tuple[str, int]:
    """A left-to-right flowchart of a lineage graph (a node per column, table or KPI, an edge
    per step labelled with its kind). Returns the diagram and how many nodes were left out."""
    nodes = graph.nodes[:MAX_LINEAGE_NODES]
    keys = {n.id: f"n{i}" for i, n in enumerate(nodes)}
    lines = ["flowchart LR"]
    for node in nodes:
        lines.append(f'    {keys[node.id]}["{label(node.label)} ({label(node.layer)})"]')
    for edge in graph.edges:
        if edge.from_id in keys and edge.to_id in keys:
            lines.append(
                f"    {keys[edge.from_id]} -->|{ident(edge.kind)}| {keys[edge.to_id]}"
            )
    return "\n".join(lines), len(graph.nodes) - len(nodes)
