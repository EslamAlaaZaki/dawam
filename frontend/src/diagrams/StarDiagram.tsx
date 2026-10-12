"use client";

import { KeyRound } from "lucide-react";
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { DwTable } from "../api/dwModel";
import { NO_PII, piiMarkLabel, type PiiMarks } from "../api/piiView";
import { PiiMarker } from "./PiiMarker";
import { isFact, linksOf, ranks } from "./starModel";

const NODE_WIDTH = 220;
const COLUMN_GAP = 60;
const ROW_GAP = 90;
const ROW_HEIGHT = 170;
const HEAD = 34;
const LINE = 18;
const MAX_LINES = 6;

/** The columns worth drawing: keys, and a fact's measures. */
function keyColumns(t: DwTable, pii: PiiMarks) {
  return t.columns.filter((c) => ["sk", "nk", "fk", "measure"].includes(c.role) || pii.has(c.id));
}

/** React Flow draws the Layer's tables as nodes and its foreign keys as role-labelled edges.
 * `pii` holds the PII view's marks by id: PII-derived columns are always drawn, marked, and a
 * table with marks (derived columns, or influenced by PII) carries a table-level marker. */
export function StarDiagram({
  tables,
  pii = NO_PII,
}: {
  tables: DwTable[];
  pii?: PiiMarks;
}) {
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
      height: HEAD + LINE * Math.min(keyColumns(t, pii).length, MAX_LINES),
      data: { label: <TableCard table={t} pii={pii} /> },
      className: isFact(t) ? "star-node star-fact" : "star-node star-dimension",
      draggable: false,
    };
  });
  const edges: Edge[] = links.map((l) => ({
    id: l.id,
    source: l.from.id,
    target: l.to.id,
    label: l.label,
    style: { stroke: "var(--sand-11)" },
    markerEnd: { type: MarkerType.ArrowClosed, color: "var(--sand-11)" },
  }));

  return (
    <div className="dawam-flow" role="group" aria-label="Star diagram" style={{ height: 520 }}>
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

function TableCard({ table, pii }: { table: DwTable; pii: PiiMarks }) {
  const derived = table.columns.filter((c) => pii.get(c.id)?.kind === "derived");
  const influenced = pii.get(table.id)?.kind === "influenced";
  const tableLabel = [
    derived.length > 0
      ? `${derived.length} PII-derived ${derived.length === 1 ? "column" : "columns"}: ${derived.map((c) => c.name).join(", ")}`
      : "",
    influenced ? "PII-influenced table" : "",
  ]
    .filter(Boolean)
    .join("; ");
  return (
    <div className="star-card">
      <strong className="star-name">{table.name}</strong>
      {tableLabel ? <PiiMarker label={tableLabel} /> : null}
      <small className="star-kind">
        {" "}
        {table.kind}
        {table.is_conformed ? " · conformed" : ""}
      </small>
      <ul>
        {keyColumns(table, pii)
          .slice(0, MAX_LINES)
          .map((c) => (
            <li key={c.id}>
              {c.role === "sk" ? (
                <KeyRound aria-hidden className="star-key" />
              ) : null}
              {c.name}
              {pii.get(c.id)?.kind === "derived" ? (
                <PiiMarker label={piiMarkLabel(pii.get(c.id)!)} />
              ) : null}
              {c.role === "sk" ? " (SK)" : c.role === "fk" ? " (FK)" : ""}
            </li>
          ))}
      </ul>
    </div>
  );
}
