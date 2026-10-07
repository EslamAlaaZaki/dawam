"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useState } from "react";

import { useAcceptedRelationships } from "../api/relationships";
import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import {
  filterGraph,
  qualified,
  relationshipEdges,
  type ErEdge,
} from "./erGraph";

const NODE_WIDTH = 180;
const NODE_HEIGHT = 40;
const COLUMNS = 4;

/** Declared foreign keys are solid; accepted inferred relationships are dashed. */
const EDGE_STYLE: Record<ErEdge["kind"], React.CSSProperties> = {
  declared: { strokeWidth: 2 },
  inferred: { strokeWidth: 2, strokeDasharray: "6 4" },
};

/**
 * A Source System's ER diagram (spec story 60): its tables and views with the foreign keys
 * the latest Snapshot declares and the inferred relationships a member accepted. Narrow it
 * by Database Schema, or focus on one table and its neighbours. Any member can view it.
 */
export function SourceErDiagram({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const schema = useSourceSchema(workspace.id, system.id, true);
  const accepted = useAcceptedRelationships(workspace.id, system.id);
  const [dbSchema, setDbSchema] = useState("");
  const [tableId, setTableId] = useState("");

  const graph = useMemo(() => {
    if (!schema.data || !accepted.data) {
      return null;
    }
    const all = relationshipEdges(schema.data.tables, accepted.data);
    return {
      all,
      shown: filterGraph(schema.data.tables, all, { dbSchema, tableId }),
    };
  }, [schema.data, accepted.data, dbSchema, tableId]);

  if (schema.isPending || accepted.isPending) {
    return <p>Loading the ER diagram…</p>;
  }
  if (schema.isError || accepted.isError) {
    const message = (schema.error ?? accepted.error)?.message;
    return <p role="alert">Could not load the ER diagram: {message}</p>;
  }
  if (!graph) {
    return null;
  }
  const { tables, edges } = graph.shown;
  const present = schema.data.tables.filter(
    (t) => !t.status || t.status === "present",
  );
  const byId = new Map(present.map((t) => [t.id, t]));

  const nodes: Node[] = tables.map((table, index) => ({
    id: table.id,
    position: {
      x: (index % COLUMNS) * (NODE_WIDTH + 60),
      y: Math.floor(index / COLUMNS) * (NODE_HEIGHT + 80),
    },
    data: { label: qualified(table) },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
    selected: table.id === tableId,
  }));
  const flowEdges: Edge[] = edges.map((edge) => ({
    id: edge.id,
    source: edge.from,
    target: edge.to,
    label: edge.label,
    style: EDGE_STYLE[edge.kind],
    animated: false,
    ariaLabel: `${edge.kind} relationship`,
  }));

  return (
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">ER diagram</h3>
      <div className="form">
        <label htmlFor="er-schema">Database Schema</label>
        <select
          id="er-schema"
          value={dbSchema}
          onChange={(event) => {
            setDbSchema(event.target.value);
            setTableId("");
          }}
        >
          <option value="">All Database Schemas</option>
          {schema.data.db_schemas.map((s) => (
            <option key={s.id} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        <label htmlFor="er-table">Focus on a table</label>
        <select
          id="er-table"
          value={tableId}
          onChange={(event) => setTableId(event.target.value)}
        >
          <option value="">All tables</option>
          {present
            .filter((t) => dbSchema === "" || t.db_schema === dbSchema)
            .map((t) => (
              <option key={t.id} value={t.id}>
                {qualified(t)}
              </option>
            ))}
        </select>
      </div>
      <p>
        Solid lines are foreign keys declared in the Snapshot; dashed lines are
        accepted inferred relationships.
      </p>
      {tables.length === 0 ? (
        <p className="empty-state">No tables match these filters.</p>
      ) : (
        <div
          className="er-diagram"
          style={{ height: 480 }}
          aria-label="ER diagram canvas"
        >
          <ReactFlow
            nodes={nodes}
            edges={flowEdges}
            nodesConnectable={false}
            nodesDraggable
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
      <ul aria-label="Relationships">
        {edges.map((edge) => (
          <li key={edge.id}>
            {qualified(byId.get(edge.from)!)} to {qualified(byId.get(edge.to)!)}
            {" ("}
            {edge.label}
            {"), "}
            {edge.kind === "declared" ? "declared" : "inferred, accepted"}
          </li>
        ))}
      </ul>
    </section>
  );
}
