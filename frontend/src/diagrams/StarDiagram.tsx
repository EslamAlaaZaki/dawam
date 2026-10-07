"use client";

import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { DwTable } from "../api/dwModel";
import { isFact, linksOf, ranks } from "./starModel";

const NODE_WIDTH = 220;
const COLUMN_GAP = 60;
const ROW_GAP = 90;
const ROW_HEIGHT = 170;
const HEAD = 34;
const LINE = 18;
const MAX_LINES = 6;

/** The columns worth drawing: keys, and a fact's measures. */
function keyColumns(t: DwTable) {
  return t.columns.filter((c) => ["sk", "nk", "fk", "measure"].includes(c.role));
}

/** React Flow draws the Layer's tables as nodes and its foreign keys as role-labelled edges. */
export function StarDiagram({ tables }: { tables: DwTable[] }) {
  const links = linksOf(tables);
  const rank = ranks(tables, links);
  const seen = new Map<number, number>();
  const nodes: Node[] = tables.map((t) => {
    const r = rank.get(t.id) ?? 0;
    const index = seen.get(r) ?? 0;
    seen.set(r, index + 1);
    return {
      id: t.id,
      position: {
        x: index * (NODE_WIDTH + COLUMN_GAP),
        y: r * (ROW_HEIGHT + ROW_GAP),
      },
      width: NODE_WIDTH,
      height: HEAD + LINE * Math.min(keyColumns(t).length, MAX_LINES),
      data: { label: <TableCard table={t} /> },
      className: isFact(t) ? "star-node star-fact" : "star-node star-dimension",
      draggable: false,
    };
  });
  const edges: Edge[] = links.map((l) => ({
    id: l.id,
    source: l.from.id,
    target: l.to.id,
    label: l.label,
    markerEnd: { type: MarkerType.ArrowClosed },
  }));

  return (
    <div className="star-diagram" role="group" aria-label="Star diagram" style={{ height: 520 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodesConnectable={false}
        elementsSelectable={false}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function TableCard({ table }: { table: DwTable }) {
  return (
    <div className="star-card">
      <strong>{table.name}</strong>
      <small>
        {" "}
        {table.kind}
        {table.is_conformed ? " · conformed" : ""}
      </small>
      <ul>
        {keyColumns(table)
          .slice(0, MAX_LINES)
          .map((c) => (
            <li key={c.id}>
              {c.name}
              {c.role === "sk" ? " (SK)" : c.role === "fk" ? " (FK)" : ""}
            </li>
          ))}
      </ul>
    </div>
  );
}
