import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwTableSummary } from "../api/dwModel";
import type { Me } from "../api/queries";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const PATH = `/workspaces/${WORKSPACE_ID}?folder=dw`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const WORKSPACE = {
  id: WORKSPACE_ID,
  name: "Retail DW",
  description: "",
  domain: "",
  role: "owner",
  permissions: ["dw_schema.edit", "workspace.view", "kpi.view_review"],
  status: "active",
  archived_at: null,
  require_distinct_approver: false,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const WAREHOUSE = {
  set_up: true,
  id: "22222222-2222-4222-8222-222222222222",
  target_platform: "postgresql",
  layer_schemas: { staging: "stg", core: "core", mart: "mart" },
  naming_rules: {
    case_style: "lower",
    dimension_prefix: "dim_",
    fact_prefix: "fct_",
    bridge_prefix: "bridge_",
    load_ts_column: "load_ts",
    source_system_column: "source_system",
    created_ts_column: "created_ts",
    updated_ts_column: "updated_ts",
  },
  date_dimension: {
    start_year: 2000,
    end_year: 2040,
    weekend_days: ["saturday", "sunday"],
    include_hijri: false,
    fiscal_year_start_month: null,
    include_time_dimension: false,
  },
  set_up_at: "2026-10-12T09:00:00Z",
  updated_at: "2026-10-12T09:00:00Z",
  version: 1,
};

let seq = 0;
function table(
  layer: string,
  name: string,
  columns: number,
  issues = 0,
): DwTableSummary {
  seq += 1;
  return {
    id: `a0000000-0000-4000-8000-00000000000${seq}`,
    layer,
    name,
    kind: layer === "staging" ? "staging" : "dimension",
    fact_type: null,
    grain: null,
    is_aggregate: false,
    scd_type: null,
    is_conformed: false,
    description: "",
    column_count: columns,
    naming_violation_count: issues,
    version: 1,
    review_flags: [],
    status: "present",
  } as DwTableSummary;
}

const TABLES: Record<string, DwTableSummary[]> = {
  staging: [table("staging", "stg_a", 10), table("staging", "stg_b", 20)],
  core: [table("core", "dim_a", 5, 1), table("core", "dim_b", 7, 2)],
  mart: [table("mart", "fct_a", 3)],
};

function failed(severity: string, layer: string) {
  return {
    check_code: `c_${severity}_${layer}`,
    title: "A check",
    category: "naming",
    severity,
    layer,
    object_type: "table",
    object_id: TABLES.core![0]!.id,
    table_id: TABLES.core![0]!.id,
    object_name: "x",
    message: "m",
    fix_hint: "",
    link: "",
  };
}

const SCORE = {
  score: 84,
  grade: "B",
  capped: false,
  calculated_at: "2026-01-05T09:00:00Z",
  layers: [
    { layer: "staging", score: 95, grade: "A", scored: true, table_count: 2 },
    { layer: "core", score: 81.2, grade: "B", scored: true, table_count: 2 },
    { layer: "mart", score: 90, grade: "A", scored: true, table_count: 1 },
  ],
  tables: [],
  failed_checks: [
    failed("error", "core"),
    failed("warning", "core"),
    failed("warning", "mart"),
    failed("info", "mart"),
    failed("error", "staging"),
  ],
  checks_passed: 0,
};

const COVERAGE = {
  coverage: { total: 100, covered: 87, percent: 87, system: 0 },
  layers: [
    {
      layer: "core",
      coverage: { total: 80, covered: 66, percent: 82.3, system: 0 },
      tables: [],
    },
    {
      layer: "mart",
      coverage: { total: 20, covered: 19, percent: 95, system: 0 },
      tables: [],
    },
  ],
};

function kpi(n: number, review_state: string) {
  return {
    id: `b0000000-0000-4000-8000-00000000000${n}`,
    name: `KPI ${n}`,
    review_state,
    status: "approved",
    origin: "user",
  };
}

function backend(warehouse: object = WAREHOUSE) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(WORKSPACE);
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [
            { layer: "staging", status: "complete" },
            { layer: "core", status: "in_progress" },
            { layer: "mart", status: "in_progress" },
          ],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/data-warehouse`:
        return json(warehouse);
      case `GET ${API}/data-warehouse/tables`:
        return json({ items: TABLES[String(r.query.layer)] ?? [] });
      case `GET ${API}/data-warehouse/score`:
        return json(SCORE);
      case `GET ${API}/data-warehouse/coverage`:
        return json(COVERAGE);
      case `GET ${API}/kpis`:
        return json({
          items: [kpi(1, "live"), kpi(2, "accepted"), kpi(3, "suggested")],
          rule_suggestions: [{}, {}],
          rejected_rule_suggestions: [],
          next_cursor: null,
        });
      case `GET ${API}/data-warehouse/files`:
        return json({
          items: [{ id: "f1", name: "a.sql", mime: "application/sql", index_status: "indexed" }],
          next_cursor: null,
        });
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

const cells = (row: HTMLElement) =>
  within(row)
    .getAllByRole("cell")
    .map((c) => c.textContent);

describe("the Data Warehouse overview", () => {
  it("shows the header and the summary line", async () => {
    renderApp(backend(), PATH);

    expect(
      await screen.findByRole("heading", { name: "Data Warehouse" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "The target model for every Source System: Staging, then Core, then Mart.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "PostgreSQL · 3 Layers · 5 tables · 45 columns · 87% of Core and Mart columns mapped · set up 12 Oct 2026",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the setup dialog from Edit setup", async () => {
    renderApp(backend(), PATH);

    fireEvent.click(await screen.findByRole("button", { name: "Edit setup" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Edit setup" }),
    ).toBeInTheDocument();
  });

  it("shows the score card and links it to the Score folder", async () => {
    renderApp(backend(), PATH);

    const card = await screen.findByRole("link", {
      name: /Score 84, grade B/,
    });
    expect(card).toHaveAttribute("href", "?folder=dw%2Fscore");
    expect(within(card).getByText("Data Warehouse score")).toBeInTheDocument();
    expect(within(card).getByText("Open Score")).toBeInTheDocument();
    expect(within(card).getByText("Grade B")).toBeInTheDocument();
    expect(within(card).getByText("81 · B")).toBeInTheDocument();
    expect(within(card).getByText("90 · A")).toBeInTheDocument();
    expect(within(card).getByText("1 error")).toBeInTheDocument();
    expect(within(card).getByText("2 warnings")).toBeInTheDocument();
    expect(within(card).getByText("1 info")).toBeInTheDocument();
    expect(
      within(card).getByText(/Core and Mart only. Staging is scored apart./),
    ).toBeInTheDocument();
  });

  it("shows the setup read-only", async () => {
    renderApp(backend(), PATH);

    const setup = await screen.findByRole("region", { name: "Setup" });
    const text = (term: string) =>
      screen.getByText(term, { selector: "dt" }).nextElementSibling
        ?.textContent;
    expect(setup).toBeInTheDocument();
    expect(text("Platform")).toBe("PostgreSQL");
    expect(text("Schemas")).toBe("stg · core · mart");
    expect(text("Prefixes")).toBe("dim_ · fct_ · bridge_");
    expect(text("Audit columns")).toBe("load_ts · source_system · created_ts");
    expect(text("Case")).toBe("lower");
    expect(text("Date dimension")).toBe("2000 to 2040, weekend Sat and Sun");
  });

  it("lists one row per Layer", async () => {
    renderApp(backend(), PATH);

    const table = await screen.findByRole("table", { name: "Layers" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map(cells)).toEqual([
      ["Staging", "stg", "Complete", "2", "30", "1:1", "0", "95 A", "Open"],
      ["Core", "core", "In progress", "2", "12", "82%", "2", "81 B", "Open"],
      ["Mart", "mart", "In progress", "1", "3", "95%", "0", "90 A", "Open"],
    ]);
  });

  it("opens a Layer from its row", async () => {
    renderApp(backend(), PATH);

    const table = await screen.findByRole("table", { name: "Layers" });
    fireEvent.click(within(table).getByRole("row", { name: /^Core/ }));

    expect(
      await screen.findByRole("heading", { name: /Core Layer/ }),
    ).toBeInTheDocument();
  });

  it("counts KPIs and files in the link tiles", async () => {
    renderApp(backend(), PATH);

    const kpis = await screen.findByRole("link", { name: /KPIs/ });
    expect(kpis).toHaveAttribute("href", "?folder=dw%2Fkpis");
    expect(
      await within(kpis).findByText("2 accepted · 3 suggested"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lineage/ })).toHaveTextContent(
      "Trace any column to its source",
    );
    expect(await screen.findByRole("link", { name: /Files/ })).toHaveTextContent(
      "1 file",
    );
    expect(screen.getByRole("link", { name: /DDL/ })).toHaveTextContent(
      "Generate for PostgreSQL",
    );
  });
});

describe("the Data Warehouse folder before setup", () => {
  it("still opens the setup dialog and shows the hint", async () => {
    renderApp(backend({ set_up: false }), PATH);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(
      await screen.findByRole("button", { name: "Open Data Warehouse setup" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit setup" }),
    ).not.toBeInTheDocument();
  });
});
