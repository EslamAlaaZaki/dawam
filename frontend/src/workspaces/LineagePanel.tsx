"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { useDwTable, useDwTables, type DwLayer } from "../api/dwModel";
import { useKpis } from "../api/kpis";
import { usePiiMarks } from "../api/piiView";
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
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Loading } from "../shell/Loading";
import {
  Actions,
  EmptyState,
  ErrorText,
  NativeSelect,
  Panel,
  h3Classes,
  h4Classes,
  h5Classes,
  labelClasses,
} from "./parts";

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
    <Panel aria-labelledby="lineage-title">
      <h3 id="lineage-title" className={h3Classes}>
        Lineage
      </h3>
      <StartPicker workspaceId={workspace.id} onPick={startFrom} />
      {start ? (
        // Another start begins with nothing expanded or selected.
        <LineageView key={start} workspaceId={workspace.id} start={start} onStart={startFrom} />
      ) : (
        <EmptyState>
          Pick a KPI or a DW column to see where it comes from and what depends on it.
        </EmptyState>
      )}
    </Panel>
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
  const pii = usePiiMarks(workspaceId);
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
    return <ErrorText>Could not load the lineage: {lineage.error.message}</ErrorText>;
  }
  return (
    <>
      <p className="text-muted-foreground">
        Lineage of <strong className="text-foreground">{lineage.data.start.label}</strong> (
        {LAYER_LABELS[lineage.data.start.layer]}): {shown.nodes.length} nodes,{" "}
        {shown.edges.length} edges.
      </p>
      <Legend />
      {shown.edges.length === 0 ? (
        <EmptyState>No lineage is recorded for this node yet.</EmptyState>
      ) : (
        <LineageGraph
          nodes={shown.nodes}
          edges={shown.edges}
          startId={lineage.data.start.id}
          selectedId={selected}
          pii={pii}
          onSelect={(id) => {
            setSelected(id);
            setImpactFor(null);
          }}
        />
      )}
      {selectedNode && (
        <aside
          aria-label="Selected node"
          className="flex flex-col gap-2.5 rounded-lg border border-border bg-surface p-4"
        >
          <h4 className={h4Classes}>
            {selectedNode.label}{" "}
            <small className="font-normal text-muted-foreground">
              ({LAYER_LABELS[selectedNode.layer]})
            </small>
          </h4>
          <Actions>
            <Button
              type="button"
              size="sm"
              disabled={expanding !== null}
              onClick={() => expand(selectedNode.id, "upstream")}
            >
              Expand upstream
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={expanding !== null}
              onClick={() => expand(selectedNode.id, "downstream")}
            >
              Expand downstream
            </Button>
            {selectedNode.id !== start && (
              <Button type="button" size="sm" onClick={() => onStart(selectedNode.id)}>
                Start from here
              </Button>
            )}
            <Button type="button" size="sm" onClick={() => setImpactFor(selectedNode.id)}>
              Impact report
            </Button>
          </Actions>
          {expandError && <ErrorText>Could not expand: {expandError}</ErrorText>}
        </aside>
      )}
      {impactFor && <ImpactPanel workspaceId={workspaceId} node={impactFor} />}
      <EdgeList edges={shown.edges} byId={byId} />
    </>
  );
}

function Legend() {
  return (
    <ul aria-label="Edge kinds" className="flex flex-wrap gap-2">
      {(Object.keys(EDGE_KIND_LABELS) as LineageEdge["kind"][]).map((kind) => (
        <li key={kind}>
          <Badge>
            {kind}: {EDGE_KIND_LABELS[kind]}
          </Badge>
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
    <details className="text-sm">
      <summary className="cursor-pointer font-medium text-muted-foreground hover:text-foreground">
        Edges as a list
      </summary>
      <ul aria-label="Lineage edges" className="mt-2 flex flex-col gap-1 font-mono text-mono">
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
    return <ErrorText>Could not load the impact report: {impact.error.message}</ErrorText>;
  }
  const { start, columns, tables, kpis } = impact.data;
  const groups: [string, LineageNode[]][] = [
    ["DW columns", columns],
    ["DW tables steered by it (joins, filters, GROUP BY, lookups)", tables],
    ["KPIs", kpis],
  ];
  return (
    <section
      aria-label="Impact report"
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4"
    >
      <h4 className={h4Classes}>Impact of {start.label}</h4>
      {columns.length + tables.length + kpis.length === 0 ? (
        <EmptyState>Nothing in the Data Warehouse depends on it.</EmptyState>
      ) : (
        groups
          .filter(([, items]) => items.length > 0)
          .map(([title, items]) => (
            <div key={title} className="flex flex-col gap-1">
              <h5 className={h5Classes}>
                {title} ({items.length})
              </h5>
              <ul aria-label={title} className="flex flex-col gap-0.5">
                {items.map((n) => (
                  <li key={n.id}>
                    {n.label}{" "}
                    <small className="text-muted-foreground">({LAYER_LABELS[n.layer]})</small>
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
    <fieldset className="m-0 flex min-w-0 flex-wrap items-end gap-3 rounded-lg border border-border p-3">
      <legend className="px-1 text-sm font-semibold text-muted-foreground">Start from</legend>
      <label className={labelClasses}>
        KPI
        <NativeSelect
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
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Layer
        <NativeSelect
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
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Table
        <NativeSelect value={tableId ?? ""} onChange={(e) => setTableId(e.target.value || null)}>
          <option value="">Choose a table…</option>
          {tables.data?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Column
        <NativeSelect
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
        </NativeSelect>
      </label>
    </fieldset>
  );
}
