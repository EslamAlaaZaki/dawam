"use client";

import {
  Background,
  Controls,
  MarkerType,
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
import { PiiMarker } from "../diagrams/PiiMarker";
import { EmptyState, Hint, NativeSelect, Panel, h3Classes, labelClasses } from "./parts";
import {
  filterGraph,
  qualified,
  relationshipEdges,
  type ErEdge,
} from "./erGraph";
import { Loading } from "../shell/Loading";

type SourceTable = NonNullable<ReturnType<typeof useSourceSchema>["data"]>["tables"][number];

const NODE_WIDTH = 180;
const NODE_HEIGHT = 40;
const COLUMNS = 4;

/** Declared foreign keys are solid; accepted inferred relationships are dashed. */
const EDGE_STYLE: Record<ErEdge["kind"], React.CSSProperties> = {
  declared: { strokeWidth: 2, stroke: "var(--sand-11)" },
  inferred: { strokeWidth: 2, strokeDasharray: "6 4", stroke: "var(--jade-11)" },
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
    return <Loading label="Loading the ER diagram…" className="p-0" />;
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
    data: { label: <ErNode table={table} /> },
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
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: edge.kind === "declared" ? "var(--sand-11)" : "var(--jade-11)",
    },
    ariaLabel: `${edge.kind} relationship`,
  }));

  return (
    <Panel aria-labelledby="folder-title">
      <h3 id="folder-title" className={h3Classes}>
        ER diagram
      </h3>
      <div className="flex max-w-xl flex-col gap-3.5">
        <label htmlFor="er-schema" className={labelClasses}>
          Database Schema
        </label>
        <NativeSelect
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
        </NativeSelect>
        <label htmlFor="er-table" className={labelClasses}>
          Focus on a table
        </label>
        <NativeSelect
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
        </NativeSelect>
      </div>
      <Hint>
        Solid lines are foreign keys declared in the Snapshot; dashed lines are
        accepted inferred relationships. A shield marks a table with Protected Columns.
      </Hint>
      {tables.length === 0 ? (
        <EmptyState>No tables match these filters.</EmptyState>
      ) : (
        <div
          className="dawam-flow"
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
      <ul aria-label="Relationships" className="text-sm text-sand-11">
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
    </Panel>
  );
}

/** A table's name, with the PII marker when it has Protected Columns (named in its label). */
function ErNode({ table }: { table: SourceTable }) {
  const protectedNames = table.columns.filter((c) => c.is_protected).map((c) => c.name);
  return (
    <span className="inline-flex items-center">
      {qualified(table)}
      {protectedNames.length > 0 && (
        <PiiMarker
          label={`${protectedNames.length} Protected ${protectedNames.length === 1 ? "Column" : "Columns"}: ${protectedNames.join(", ")}`}
        />
      )}
    </span>
  );
}
