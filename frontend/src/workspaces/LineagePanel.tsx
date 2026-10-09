"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { useDwTable, useDwTables, type DwLayer } from "../api/dwModel";
import { useKpis } from "../api/kpis";
import {
  EDGE_KIND_LABELS,
  LAYER_LABELS,
  useFetchLineage,
  useImpact,
  useLineage,
  type LineageDirection,
  type LineageEdge,
  type LineageNode,
} from "../api/lineage";
import type { Workspace } from "../api/workspaces";
import { LineageGraph } from "../diagrams/LineageGraph";
import { merge } from "../diagrams/lineageLayout";
import { Loading } from "../shell/Loading";

const LAYERS: DwLayer[] = ["staging", "core", "mart"];

interface Shown {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

/**
 * The lineage folder: pick a KPI or a DW column (or arrive with `node` in the URL), see its
 * lineage from source columns through Staging, Core and Mart to KPIs, expand any node
 * upstream or downstream, and open the impact report of a node.
 */
export function LineagePanel({ workspace }: { workspace: Workspace }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const start = searchParams.get("node");

  const startFrom = useCallback(
    (node: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("node", node);
      router.push(`${pathname}?${params}`);
    },
    [pathname, router, searchParams],
  );

  return (
    <section aria-labelledby="lineage-title" className="lineage-panel">
      <h3 id="lineage-title">Lineage</h3>
      <StartPicker workspaceId={workspace.id} onPick={startFrom} />
      {start ? (
        // Another start begins with nothing expanded or selected.
        <LineageView key={start} workspaceId={workspace.id} start={start} onStart={startFrom} />
      ) : (
        <p className="empty-state">
          Pick a KPI or a DW column to see where it comes from and what depends on it.
        </p>
      )}
    </section>
  );
}

function LineageView({
  workspaceId,
  start,
  onStart,
}: {
  workspaceId: string;
  start: string;
  onStart: (node: string) => void;
}) {
  const lineage = useLineage(workspaceId, { node: start, direction: "both" });
  const fetchLineage = useFetchLineage(workspaceId);
  const [added, setAdded] = useState<Shown>({ nodes: [], edges: [] });
  const [selected, setSelected] = useState<string | null>(start);
  const [impactFor, setImpactFor] = useState<string | null>(null);
  const [expanding, setExpanding] = useState<string | null>(null);
  const [expandError, setExpandError] = useState<string | null>(null);

  const shown: Shown = lineage.data ? merge(lineage.data, added) : added;
  const byId = new Map(shown.nodes.map((n) => [n.id, n]));
  const selectedNode = selected ? byId.get(selected) : undefined;

  async function expand(node: string, direction: LineageDirection) {
    setExpanding(`${node}:${direction}`);
    setExpandError(null);
    try {
      const more = await fetchLineage({ node, direction });
      setAdded((current) => merge(current, more));
    } catch (error) {
      setExpandError(error instanceof Error ? error.message : String(error));
    } finally {
      setExpanding(null);
    }
  }

  if (lineage.isPending) {
    return <Loading />;
  }
  if (lineage.isError) {
    return <p role="alert">Could not load the lineage: {lineage.error.message}</p>;
  }
  return (
    <>
      <p>
        Lineage of <strong>{lineage.data.start.label}</strong> (
        {LAYER_LABELS[lineage.data.start.layer]}): {shown.nodes.length} nodes,{" "}
        {shown.edges.length} edges.
      </p>
      <Legend />
      {shown.edges.length === 0 ? (
        <p className="empty-state">No lineage is recorded for this node yet.</p>
      ) : (
        <LineageGraph
          nodes={shown.nodes}
          edges={shown.edges}
          startId={lineage.data.start.id}
          selectedId={selected}
          onSelect={(id) => {
            setSelected(id);
            setImpactFor(null);
          }}
        />
      )}
      {selectedNode && (
        <aside aria-label="Selected node" className="lineage-selection">
          <h4>
            {selectedNode.label} <small>({LAYER_LABELS[selectedNode.layer]})</small>
          </h4>
          <div className="actions">
            <button
              type="button"
              disabled={expanding !== null}
              onClick={() => expand(selectedNode.id, "upstream")}
            >
              Expand upstream
            </button>
            <button
              type="button"
              disabled={expanding !== null}
              onClick={() => expand(selectedNode.id, "downstream")}
            >
              Expand downstream
            </button>
            {selectedNode.id !== start && (
              <button type="button" onClick={() => onStart(selectedNode.id)}>
                Start from here
              </button>
            )}
            <button type="button" onClick={() => setImpactFor(selectedNode.id)}>
              Impact report
            </button>
          </div>
          {expandError && <p role="alert">Could not expand: {expandError}</p>}
        </aside>
      )}
      {impactFor && <ImpactPanel workspaceId={workspaceId} node={impactFor} />}
      <EdgeList edges={shown.edges} byId={byId} />
    </>
  );
}

function Legend() {
  return (
    <ul className="lineage-legend" aria-label="Edge kinds">
      {(Object.keys(EDGE_KIND_LABELS) as LineageEdge["kind"][]).map((kind) => (
        <li key={kind} className={`lineage-legend-${kind}`}>
          {kind}: {EDGE_KIND_LABELS[kind]}
        </li>
      ))}
    </ul>
  );
}

/** The edges as text, so the graph reads without the diagram too. */
function EdgeList({ edges, byId }: { edges: LineageEdge[]; byId: Map<string, LineageNode> }) {
  if (edges.length === 0) {
    return null;
  }
  return (
    <details>
      <summary>Edges as a list</summary>
      <ul aria-label="Lineage edges">
        {edges.map((e) => (
          <li key={e.id}>
            {byId.get(e.from_id)?.label} → {byId.get(e.to_id)?.label} ({e.kind})
          </li>
        ))}
      </ul>
    </details>
  );
}

function ImpactPanel({ workspaceId, node }: { workspaceId: string; node: string }) {
  const impact = useImpact(workspaceId, node);
  if (impact.isPending) {
    return <Loading />;
  }
  if (impact.isError) {
    return <p role="alert">Could not load the impact report: {impact.error.message}</p>;
  }
  const { start, columns, tables, kpis } = impact.data;
  const groups: [string, LineageNode[]][] = [
    ["DW columns", columns],
    ["DW tables steered by it (joins, filters, GROUP BY, lookups)", tables],
    ["KPIs", kpis],
  ];
  return (
    <section aria-label="Impact report" className="lineage-impact">
      <h4>Impact of {start.label}</h4>
      {columns.length + tables.length + kpis.length === 0 ? (
        <p className="empty-state">Nothing in the Data Warehouse depends on it.</p>
      ) : (
        groups
          .filter(([, items]) => items.length > 0)
          .map(([title, items]) => (
            <div key={title}>
              <h5>
                {title} ({items.length})
              </h5>
              <ul aria-label={title}>
                {items.map((n) => (
                  <li key={n.id}>
                    {n.label} <small>({LAYER_LABELS[n.layer]})</small>
                  </li>
                ))}
              </ul>
            </div>
          ))
      )}
    </section>
  );
}

/** Start from a Data Warehouse KPI, or from a column of a DW table. */
function StartPicker({
  workspaceId,
  onPick,
}: {
  workspaceId: string;
  onPick: (node: string) => void;
}) {
  const kpis = useKpis(workspaceId, { systemId: null });
  const [layer, setLayer] = useState<DwLayer>("mart");
  const [tableId, setTableId] = useState<string | null>(null);
  const tables = useDwTables(workspaceId, layer);
  const table = useDwTable(workspaceId, tableId);

  return (
    <fieldset className="lineage-picker">
      <legend>Start from</legend>
      <label>
        KPI{" "}
        <select
          value=""
          onChange={(e) => e.target.value && onPick(e.target.value)}
          disabled={!kpis.data?.length}
        >
          <option value="">Choose a KPI…</option>
          {kpis.data?.map((k) => (
            <option key={k.id} value={k.id}>
              {k.name}
            </option>
          ))}
        </select>
      </label>{" "}
      <label>
        Layer{" "}
        <select
          value={layer}
          onChange={(e) => {
            setLayer(e.target.value as DwLayer);
            setTableId(null);
          }}
        >
          {LAYERS.map((l) => (
            <option key={l} value={l}>
              {LAYER_LABELS[l]}
            </option>
          ))}
        </select>
      </label>{" "}
      <label>
        Table{" "}
        <select value={tableId ?? ""} onChange={(e) => setTableId(e.target.value || null)}>
          <option value="">Choose a table…</option>
          {tables.data?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </label>{" "}
      <label>
        Column{" "}
        <select
          value=""
          disabled={!table.data}
          onChange={(e) => e.target.value && onPick(e.target.value)}
        >
          <option value="">Choose a column…</option>
          {table.data?.columns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
