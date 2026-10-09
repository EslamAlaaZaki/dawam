import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ImpactReport, LineageNode, NodeLineage } from "../api/lineage";
import type { Me } from "../api/queries";
import { LineageGraph } from "../diagrams/LineageGraph";
import { edgeStyle, layout, merge } from "../diagrams/lineageLayout";
import { json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const DW = `/api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const id = (n: number) => `a0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const node = (n: number, type: LineageNode["type"], label: string, layer: LineageNode["layer"]) =>
  ({ id: id(n), type, label, layer }) satisfies LineageNode;

const SRC = node(1, "src_column", "crm.dbo.orders.amount", "source");
const STATUS = node(2, "src_column", "crm.dbo.orders.status", "source");
const STG = node(3, "dw_column", "stg_orders.amount", "staging");
const STG_STATUS = node(4, "dw_column", "stg_orders.status", "staging");
const CORE_TABLE = node(5, "dw_table", "fact_orders", "core");
const CORE = node(6, "dw_column", "fact_orders.amount_c", "core");
const MART = node(7, "dw_column", "mart_orders.amount_m", "mart");
const KPI = node(8, "kpi", "Revenue", "kpi");
const OTHER = node(9, "dw_column", "mart_returns.amount_r", "mart");

const edge = (n: number, kind: NodeLineage["edges"][number]["kind"], from: LineageNode, to: LineageNode) => ({
  id: id(100 + n),
  kind,
  from_id: from.id,
  to_id: to.id,
});

const KPI_LINEAGE: NodeLineage = {
  start: KPI,
  nodes: [SRC, STATUS, STG, STG_STATUS, CORE_TABLE, CORE, MART, KPI],
  edges: [
    edge(1, "value", SRC, STG),
    edge(2, "value", STATUS, STG_STATUS),
    edge(3, "uses", STG_STATUS, CORE_TABLE),
    edge(4, "value", STG, CORE),
    edge(5, "value", CORE, MART),
    edge(6, "kpi", MART, KPI),
  ],
};

const SRC_DOWNSTREAM: NodeLineage = {
  start: SRC,
  nodes: [SRC, STG, OTHER],
  edges: [edge(1, "value", SRC, STG), edge(7, "value", STG, OTHER)],
};

const SRC_IMPACT: ImpactReport = { start: SRC, columns: [STG, CORE, MART, OTHER], tables: [], kpis: [KPI] };

function backend(seen: ApiRequest[] = []) {
  return (r: ApiRequest) => {
    seen.push(r);
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json({
          id: WORKSPACE_ID,
          name: "Retail DW",
          description: "",
          domain: "",
          role: "viewer",
          permissions: ["workspace.view"],
          status: "active",
          archived_at: null,
          version: 1,
          created_at: "2026-01-05T09:00:00Z",
          updated_at: "2026-01-05T09:00:00Z",
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET ${DW}`:
        return json({ set_up: true, version: 1 });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/kpis`:
        return json({ items: [], next_cursor: null });
      case `GET ${DW}/tables`:
        return json({ items: [] });
      case `GET ${DW}/lineage`:
        return json(r.query.node === SRC.id ? SRC_DOWNSTREAM : KPI_LINEAGE);
      case `GET ${DW}/impact`:
        return json(SRC_IMPACT);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the lineage folder", () => {
  it("asks where to start without a node", async () => {
    renderApp(backend(), `${PATH}?folder=dw/lineage`);

    expect(await screen.findByText(/Pick a KPI or a DW column/)).toBeInTheDocument();
  });

  it("traces a KPI back to its source columns, uses edges told apart", async () => {
    const seen: ApiRequest[] = [];
    renderApp(backend(seen), `${PATH}?folder=dw/lineage&node=${KPI.id}`);

    const graph = await screen.findByRole("group", { name: "Lineage graph" });
    expect(within(graph).getByText("crm.dbo.orders.amount")).toBeInTheDocument();
    expect(within(graph).getByText("Revenue")).toBeInTheDocument();
    expect(screen.getByText(/8 nodes, 6 edges/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Lineage edges" });
    expect(within(list).getByText("stg_orders.status → fact_orders (uses)")).toBeInTheDocument();
    const request = seen.find((r) => r.path === `${DW}/lineage`);
    expect(request?.query).toMatchObject({ node: KPI.id, direction: "both" });
  });

  it("expands a node downstream and opens its impact report", async () => {
    renderApp(backend(), `${PATH}?folder=dw/lineage&node=${KPI.id}`);
    const graph = await screen.findByRole("group", { name: "Lineage graph" });

    fireEvent.click(within(graph).getByRole("button", { name: "crm.dbo.orders.amount" }));
    const selection = screen.getByRole("complementary", { name: "Selected node" });
    fireEvent.click(within(selection).getByRole("button", { name: "Expand downstream" }));
    expect(await within(graph).findByText("mart_returns.amount_r")).toBeInTheDocument();
    expect(screen.getByText(/9 nodes, 7 edges/)).toBeInTheDocument();

    fireEvent.click(within(selection).getByRole("button", { name: "Impact report" }));
    const impact = await screen.findByRole("region", { name: "Impact report" });
    expect(within(within(impact).getByRole("list", { name: "KPIs" })).getByText("Revenue")).toBeInTheDocument();
    expect(within(impact).getByRole("list", { name: "DW columns" }).children).toHaveLength(4);
  });
});

describe("the lineage layout", () => {
  it("puts each Layer in its own column, source first", () => {
    const placed = layout([KPI, MART, SRC, STG]);

    expect(placed.map((p) => p.node.layer)).toEqual(["source", "staging", "mart", "kpi"]);
    expect(new Set(placed.map((p) => p.x)).size).toBe(4);
  });

  it("draws uses and lookup edges apart from value edges", () => {
    expect(edgeStyle("value").strokeDasharray).toBeUndefined();
    expect(edgeStyle("uses").strokeDasharray).toBeDefined();
    expect(edgeStyle("lookup").strokeDasharray).toBeDefined();
    expect(edgeStyle("lookup").strokeDasharray).not.toEqual(edgeStyle("uses").strokeDasharray);
  });

  it("merges graphs, each node and edge once", () => {
    const merged = merge(KPI_LINEAGE, SRC_DOWNSTREAM);

    expect(merged.nodes).toHaveLength(9);
    expect(merged.edges).toHaveLength(7);
  });

  it("renders a 500-node graph in under 2 s", () => {
    const layers: LineageNode["layer"][] = ["source", "staging", "core", "mart", "kpi"];
    const nodes = Array.from({ length: 500 }, (_, i) =>
      node(1000 + i, "dw_column", `t.c${i}`, layers[i % layers.length] as LineageNode["layer"]),
    );
    const edges = nodes.slice(1).map((n, i) => edge(1000 + i, "value", nodes[i] as LineageNode, n));

    const started = performance.now();
    render(
      <LineageGraph nodes={nodes} edges={edges} startId={id(1000)} selectedId={null} onSelect={() => {}} />,
    );
    expect(screen.getByRole("group", { name: "Lineage graph" })).toBeInTheDocument();

    expect(performance.now() - started).toBeLessThan(2000);
  });
});
