import { fireEvent, screen, within } from "@testing-library/react";
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
const CORE = `/workspaces/${WORKSPACE_ID}?folder=dw/core`;
const MART = `/workspaces/${WORKSPACE_ID}?folder=dw/mart`;

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
  permissions: ["dw_schema.edit", "workspace.view"],
  status: "active",
  archived_at: null,
  require_distinct_approver: false,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

function table(
  n: number,
  layer: "core" | "mart",
  name: string,
  extra: Partial<DwTableSummary> = {},
): DwTableSummary {
  return {
    id: `a0000000-0000-4000-8000-00000000000${n}`,
    layer,
    name,
    kind: "dimension",
    fact_type: null,
    grain: null,
    is_aggregate: false,
    scd_type: null,
    is_conformed: false,
    description: "",
    column_count: 4,
    naming_violation_count: 0,
    version: 1,
    review_flags: [],
    status: "present",
    ...extra,
  } as DwTableSummary;
}

const CORE_TABLES = [
  table(1, "core", "dim_customer", {
    scd_type: 2,
    is_conformed: true,
    column_count: 24,
  }),
  table(2, "core", "promotion_dim", {
    scd_type: 1,
    naming_violation_count: 1,
    column_count: 9,
  }),
  table(3, "core", "dim_date", {
    kind: "generated",
    is_conformed: true,
    column_count: 21,
  }),
  table(4, "core", "fct_sales", {
    kind: "fact",
    fact_type: "transactional",
    grain: "One row per receipt line",
    column_count: 18,
  }),
  table(5, "core", "bridge_customer_segment", {
    kind: "bridge",
    column_count: 5,
  }),
];

const MART_TABLES = [
  table(6, "mart", "fct_sales", {
    kind: "fact",
    fact_type: "transactional",
    grain: "One row per receipt line",
    column_count: 16,
  }),
  table(7, "mart", "fct_sales_daily", {
    kind: "fact",
    fact_type: "periodic_snapshot",
    is_aggregate: true,
    column_count: 11,
  }),
];

function failed(
  severity: string,
  layer: string,
  object_name: string,
  message: string,
) {
  return {
    check_code: `c_${object_name}`,
    title: "A check",
    category: "naming",
    severity,
    layer,
    object_type: "table",
    object_id: CORE_TABLES[0]?.id,
    table_id: CORE_TABLES[0]?.id,
    object_name,
    message,
    fix_hint: "",
    link: "",
  };
}

const SCORE = {
  score: 80,
  grade: "B",
  capped: false,
  calculated_at: "2026-01-05T09:00:00Z",
  layers: [
    { layer: "staging", score: 72, grade: "C", scored: true, table_count: 4 },
    { layer: "core", score: 81.2, grade: "B", scored: true, table_count: 5 },
    { layer: "mart", score: null, grade: null, scored: false, table_count: 2 },
  ],
  tables: [
    { table_id: CORE_TABLES[0]?.id, layer: "core", name: "dim_customer", score: 92 },
    { table_id: CORE_TABLES[1]?.id, layer: "core", name: "promotion_dim", score: 80 },
    { table_id: CORE_TABLES[2]?.id, layer: "core", name: "dim_date", score: 100 },
  ],
  failed_checks: [
    failed("error", "core", "fct_sales.store_key", "Foreign key has no match."),
    failed("warning", "core", "dim_product", "No current-row flag."),
    failed("warning", "core", "promotion_dim", "Name breaks the prefix rule."),
    failed("warning", "core", "dim_supplier.vat_no", "No description."),
    failed("info", "core", "dim_store", "Fifth check, left out of the top four."),
    failed("warning", "mart", "fct_sales_daily", "Table has no description."),
    failed("error", "staging", "stg_x", "Not a Core check."),
  ],
  checks_passed: 0,
};

const COVERAGE = {
  coverage: { total: 0, covered: 0, percent: 0, system: 0 },
  layers: [
    {
      layer: "core",
      coverage: { total: 186, covered: 153, percent: 82.3, system: 8 },
      tables: [
        {
          table_id: CORE_TABLES[0]?.id,
          table_name: "dim_customer",
          layer: "core",
          coverage: { total: 24, covered: 24, percent: 100, system: 0 },
          branch_count: 1,
        },
        {
          table_id: CORE_TABLES[3]?.id,
          table_name: "fct_sales",
          layer: "core",
          coverage: { total: 18, covered: 9, percent: 50, system: 0 },
          branch_count: 1,
        },
      ],
    },
    {
      layer: "mart",
      coverage: { total: 44, covered: 42, percent: 95, system: 2 },
      tables: [],
    },
  ],
};

function backend(tables: { core: DwTableSummary[]; mart: DwTableSummary[] }) {
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
            { layer: "core", status: "in_progress" },
            { layer: "mart", status: "in_progress" },
          ],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/data-warehouse`:
        return json({
          set_up: true,
          target_platform: "postgresql",
          layer_schemas: { staging: "staging", core: "core", mart: "mart" },
          version: 1,
        });
      case `GET ${API}/data-warehouse/tables`:
        return json({
          items:
            r.query.layer === "core"
              ? tables.core
              : r.query.layer === "mart"
                ? tables.mart
                : [],
        });
      case `GET ${API}/data-warehouse/score`:
        return json(SCORE);
      case `GET ${API}/data-warehouse/coverage`:
        return json(COVERAGE);
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

const all = { core: CORE_TABLES, mart: MART_TABLES };

describe("the Core overview", () => {
  it("sums up the Layer in the header", async () => {
    renderApp(backend(all), CORE);

    const heading = await screen.findByRole("heading", { name: /Core Layer/ });
    expect(within(heading).getByText("In progress")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The integrated model: conformed dimensions and facts built from the Staging Tables.",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent === "5 tables · 77 columns · schema core · PostgreSQL",
      ),
    ).toBeInTheDocument();
  });

  it("shows the Model card with the kind split and naming issues", async () => {
    renderApp(backend(all), CORE);

    const model = await screen.findByRole("link", { name: /^Model: 5 tables/ });
    expect(model).toHaveAttribute("href", "?folder=dw%2Fcore%2Fmodel");
    expect(within(model).getByText(/dimensions/)).toHaveTextContent(
      "2 dimensions",
    );
    expect(within(model).getByText(/facts?$/)).toHaveTextContent("1 fact");
    expect(within(model).getByText(/bridge/)).toHaveTextContent("1 bridge");
    expect(within(model).getByText(/generated/)).toHaveTextContent(
      "1 generated",
    );
    expect(within(model).queryByText(/aggregate/)).not.toBeInTheDocument();
    expect(within(model).getByText("1 with naming issues")).toBeInTheDocument();
  });

  it("shows the Mappings card with the Layer's coverage", async () => {
    renderApp(backend(all), CORE);

    const mappings = await screen.findByRole("link", {
      name: /^Mappings: 82% mapped/,
    });
    expect(mappings).toHaveAttribute("href", "?folder=dw%2Fcore%2Fmappings");
    expect(within(mappings).getByText("82%")).toBeInTheDocument();
    expect(
      within(mappings).getByText("153 of 186 columns · 8 system columns"),
    ).toBeInTheDocument();
  });

  it("shows the Evaluation card with this Layer's score and checks", async () => {
    renderApp(backend(all), CORE);

    const evaluation = await screen.findByRole("link", {
      name: /^Evaluation: score 81/,
    });
    expect(evaluation).toHaveAttribute(
      "href",
      "?folder=dw%2Fcore%2Fevaluation",
    );
    expect(within(evaluation).getByText("Grade B")).toBeInTheDocument();
    expect(within(evaluation).getByText("1 error")).toBeInTheDocument();
    expect(within(evaluation).getByText("3 warnings")).toBeInTheDocument();
    expect(within(evaluation).getByText("1 info")).toBeInTheDocument();
  });

  it("lists the dimensions with SCD, conformed, mapped and score", async () => {
    renderApp(backend(all), CORE);

    const dims = await screen.findByRole("table", { name: "Dimensions" });
    expect(
      cells(within(dims).getByRole("row", { name: /dim_customer/ })),
    ).toEqual(["dim_customer", "Type 2", "Conformed", "24", "100%", "0", "92"]);
    expect(
      cells(within(dims).getByRole("row", { name: /promotion_dim/ })),
    ).toEqual(["promotion_dim", "Type 1", "-", "9", "-", "1", "80"]);
    // Generated: a badge, no SCD, no mapping.
    expect(cells(within(dims).getByRole("row", { name: /dim_date/ }))).toEqual([
      "dim_dateGenerated",
      "-",
      "Conformed",
      "21",
      "-",
      "0",
      "100",
    ]);
  });

  it("lists the facts and bridges", async () => {
    renderApp(backend(all), CORE);

    const facts = await screen.findByRole("table", {
      name: "Facts and bridges",
    });
    expect(
      cells(within(facts).getByRole("row", { name: /fct_sales/ })),
    ).toEqual([
      "fct_sales",
      "Transactional",
      "One row per receipt line",
      "18",
      "50%",
    ]);
    expect(
      cells(
        within(facts).getByRole("row", { name: /bridge_customer_segment/ }),
      ),
    ).toEqual(["bridge_customer_segment", "Bridge", "-", "5", "-"]);
  });

  it("shows the top four failed checks of the Layer", async () => {
    renderApp(backend(all), CORE);

    const section = (
      await screen.findByRole("heading", { name: "Top failed checks" })
    ).closest("section") as HTMLElement;
    const items = within(section).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent(
      "Errorfct_sales.store_keyForeign key has no match.",
    );
    expect(within(section).queryByText(/Fifth check/)).not.toBeInTheDocument();
    expect(within(section).queryByText(/stg_x/)).not.toBeInTheDocument();
    expect(
      within(section).getByRole("link", { name: "All in Evaluation" }),
    ).toHaveAttribute("href", "?folder=dw%2Fcore%2Fevaluation");
  });

  it("opens a sub-folder from its card", async () => {
    renderApp(backend(all), CORE);

    fireEvent.click(
      await screen.findByRole("link", { name: /^Model: 5 tables/ }),
    );
    expect(await screen.findByText("Core model")).toBeInTheDocument();
  });

  it("shows an empty state that opens the Model page", async () => {
    renderApp(backend({ core: [], mart: [] }), CORE);

    expect(await screen.findByText("No Core tables yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Open Model/ }));
    expect(await screen.findByText("Core model")).toBeInTheDocument();
  });
});

describe("the Mart overview", () => {
  it("has no dimensions, a Facts table with an Aggregate badge and its own checks", async () => {
    renderApp(backend(all), MART);

    expect(
      await screen.findByRole("heading", { name: /Mart Layer/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Facts and aggregates shaped for reporting and KPIs/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Dimensions" })).toBeNull();

    const model = await screen.findByRole("link", { name: /^Model: 2 tables/ });
    expect(within(model).getByText(/facts/)).toHaveTextContent("2 facts");
    expect(within(model).getByText(/aggregate/)).toHaveTextContent(
      "1 aggregate",
    );

    const mappings = await screen.findByRole("link", { name: /^Mappings/ });
    expect(
      within(mappings).getByText("42 of 44 columns · 2 system columns"),
    ).toBeInTheDocument();
    const evaluation = await screen.findByRole("link", {
      name: /^Evaluation: not scored/,
    });
    expect(within(evaluation).getByText("Not scored")).toBeInTheDocument();
    expect(within(evaluation).getByText("1 warning")).toBeInTheDocument();

    const facts = await screen.findByRole("table", { name: "Facts" });
    expect(
      cells(within(facts).getByRole("row", { name: /fct_sales_daily/ })),
    ).toEqual(["fct_sales_dailyAggregate", "Periodic snapshot", "-", "11", "-"]);
    expect(
      await screen.findByText("Table has no description."),
    ).toBeInTheDocument();
  });

  it("says No Mart tables yet when empty", async () => {
    renderApp(backend({ core: CORE_TABLES, mart: [] }), MART);
    expect(await screen.findByText("No Mart tables yet")).toBeInTheDocument();
  });
});
