import type { LineageEdge, LineageNode } from "../api/lineage";

export const LAYER_ORDER: LineageNode["layer"][] = ["source", "staging", "core", "mart", "kpi"];
export const NODE_WIDTH = 230;
export const NODE_HEIGHT = 40;
const COLUMN_GAP = 90;
const ROW_GAP = 14;

export interface Placed {
  node: LineageNode;
  x: number;
  y: number;
}

/** One column per Layer, source on the left and KPIs on the right; tables sit with their
 * Layer's columns. Linear in the number of nodes, so 500 nodes lay out at once. */
export function layout(nodes: LineageNode[]): Placed[] {
  const rows = new Map<LineageNode["layer"], number>();
  const sorted = [...nodes].sort(
    (a, b) =>
      LAYER_ORDER.indexOf(a.layer) - LAYER_ORDER.indexOf(b.layer) ||
      a.label.localeCompare(b.label),
  );
  return sorted.map((node) => {
    const row = rows.get(node.layer) ?? 0;
    rows.set(node.layer, row + 1);
    return {
      node,
      x: LAYER_ORDER.indexOf(node.layer) * (NODE_WIDTH + COLUMN_GAP),
      y: row * (NODE_HEIGHT + ROW_GAP),
    };
  });
}

/** `value` and `kpi` edges are solid; `uses` dashed and `lookup` dotted (spec §6.14). */
export function edgeStyle(kind: LineageEdge["kind"]): {
  strokeDasharray?: string;
  stroke: string;
} {
  switch (kind) {
    case "uses":
      return { strokeDasharray: "6 4", stroke: "#b26a00" };
    case "lookup":
      return { strokeDasharray: "2 4", stroke: "#6a3fb2" };
    case "kpi":
      return { stroke: "#1f7a3f" };
    default:
      return { stroke: "#4a5568" };
  }
}

/** Two graphs as one, each node and edge once (expanding adds to what is shown). */
export function merge(
  a: { nodes: LineageNode[]; edges: LineageEdge[] },
  b: { nodes: LineageNode[]; edges: LineageEdge[] },
): { nodes: LineageNode[]; edges: LineageEdge[] } {
  const nodes = new Map(a.nodes.map((n) => [n.id, n]));
  b.nodes.forEach((n) => nodes.set(n.id, n));
  const edges = new Map(a.edges.map((e) => [e.id, e]));
  b.edges.forEach((e) => edges.set(e.id, e));
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}
