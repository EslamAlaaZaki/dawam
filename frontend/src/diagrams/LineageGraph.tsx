"use client";

import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { LAYER_LABELS, type LineageEdge, type LineageNode } from "../api/lineage";
import { NODE_HEIGHT, NODE_WIDTH, edgeStyle, layout } from "./lineageLayout";

/** React Flow draws the lineage graph, one column per Layer; `uses` and `lookup` edges are
 * drawn dashed and dotted, apart from the solid `value` edges. */
export function LineageGraph({
  nodes,
  edges,
  startId,
  selectedId,
  onSelect,
}: {
  nodes: LineageNode[];
  edges: LineageEdge[];
  startId: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
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
    [nodes, startId, selectedId, onSelect],
  );
  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((e) => ({
        id: e.id,
        source: e.from_id,
        target: e.to_id,
        style: edgeStyle(e.kind),
        className: `lineage-edge lineage-edge-${e.kind}`,
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
    [edges],
  );

  return (
    <div className="lineage-graph" role="group" aria-label="Lineage graph" style={{ height: 560 }}>
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
