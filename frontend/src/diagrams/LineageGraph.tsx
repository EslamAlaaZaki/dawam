"use client";

import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { NO_PII, piiMarkLabel, type PiiMarks } from "../api/piiView";
import { LAYER_LABELS, type LineageEdge, type LineageNode } from "../api/lineage";
import { PiiMarker } from "./PiiMarker";
import { NODE_HEIGHT, NODE_WIDTH, edgeStyle, layout } from "./lineageLayout";

/** React Flow draws the lineage graph, one column per Layer; `uses` and `lookup` edges are
 * drawn dashed and dotted, apart from the solid `value` edges. */
export function LineageGraph({
  nodes,
  edges,
  startId,
  selectedId,
  onSelect,
  pii = NO_PII,
}: {
  nodes: LineageNode[];
  edges: LineageEdge[];
  startId: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  /** The PII view's marks, matched to nodes by id. */
  pii?: PiiMarks;
}) {
  const flowNodes: Node[] = useMemo(
    () =>
      layout(nodes).map(({ node, x, y }) => ({
        id: node.id,
        position: { x, y },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        data: {
          label: (
            <button
              type="button"
              className="lineage-node-button"
              title={`${LAYER_LABELS[node.layer]}: ${node.label}`}
              aria-pressed={node.id === selectedId}
              onClick={() => onSelect(node.id)}
            >
              {node.label}
              {pii.has(node.id) ? <PiiMarker label={piiMarkLabel(pii.get(node.id)!, node.type === "kpi" ? "KPI" : "")} /> : null}
            </button>
          ),
        },
        className: [
          "lineage-node",
          `lineage-${node.type}`,
          node.id === startId ? "lineage-start" : "",
          node.id === selectedId ? "lineage-selected" : "",
        ].join(" "),
        draggable: false,
      })),
    [nodes, startId, selectedId, onSelect, pii],
  );
  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.from_id,
        target: e.to_id,
        style: edgeStyle(e.kind),
        className: `lineage-edge lineage-edge-${e.kind}`,
        markerEnd: { type: MarkerType.ArrowClosed, color: edgeStyle(e.kind).stroke },
      })),
    [edges],
  );

  return (
    <div className="lineage-graph dawam-flow" role="group" aria-label="Lineage graph" style={{ height: 560 }}>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodesConnectable={false}
        elementsSelectable={false}
        onlyRenderVisibleElements
        minZoom={0.05}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
