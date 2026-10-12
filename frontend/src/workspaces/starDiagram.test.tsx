import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwColumn, DwTable, DwTableSummary } from "../api/dwModel";
import type { Me } from "../api/queries";
import { json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const TABLES = `/api/v1/workspaces/${WORKSPACE_ID}/data-warehouse/tables`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const id = (n: number) => `a0000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

function column(n: number, tableId: string, name: string, over: Partial<DwColumn>): DwColumn {
  return {
    id: `c0000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    table_id: tableId,
    name,
    ordinal: n,
    data_type: { type: "bigint", length: null, precision: null, scale: null },
    is_nullable: false,
    role: "attribute",
    additivity: null,
    scd_type_override: null,
    references_table_id: null,
    role_name: null,
    description: "",
    semantic_type: null,
    is_system: false,
    naming_violations: [],
    review_flags: [],
    status: "present",
    version: 1,
    ...over,
  };
}

function table(n: number, name: string, over: Partial<DwTable>): DwTable {
  return {
    id: id(n),
    layer: "core",
    name,
    kind: "dimension",
    fact_type: null,
    grain: null,
    is_aggregate: false,
    scd_type: 1,
    is_conformed: false,
    unknown_member: null,
    description: "",
    columns: [],
    naming_violations: [],
    review_flags: [],
    status: "present",
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    version: 1,
    ...over,
  };
}

const DATE = table(1, "dim_date", { is_conformed: true });
const CUSTOMER = table(2, "dim_customer", { is_conformed: true });
const PRODUCT = table(3, "dim_product", { is_conformed: false });
const SALES = table(4, "fact_sales", {
  kind: "fact",
  fact_type: "transactional",
  scd_type: null,
  columns: [
    column(1, id(4), "order_date_key", {
      role: "fk",
      references_table_id: id(1),
      role_name: "order date",
    }),
    column(2, id(4), "ship_date_key", {
      role: "fk",
      references_table_id: id(1),
      role_name: "ship date",
    }),
    column(3, id(4), "customer_key", {
      role: "fk",
      references_table_id: id(2),
    }),
    column(4, id(4), "product_key", { role: "fk", references_table_id: id(3) }),
    column(5, id(4), "amount", { role: "measure" }),
  ],
});
const RETURNS = table(5, "fact_returns", {
  kind: "fact",
  fact_type: "transactional",
  scd_type: null,
  columns: [
    column(6, id(5), "customer_key", {
      role: "fk",
      references_table_id: id(2),
    }),
  ],
});

function summary(t: DwTable): DwTableSummary {
  const { columns, unknown_member, created_at, updated_at, naming_violations, ...rest } = t;
  void unknown_member;
  void created_at;
  void updated_at;
  void naming_violations;
  return { ...rest, column_count: columns.length, naming_violation_count: 0 };
}

function backend(tables: DwTable[], pii: { id: string; pseudonymised: boolean }[] = []) {
  return (r: ApiRequest) => {
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
          require_distinct_approver: false,
          version: 1,
          created_at: "2026-01-05T09:00:00Z",
          updated_at: "2026-01-05T09:00:00Z",
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`:
        return json({ set_up: true, version: 1 });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse/pii`:
        return json({
          columns: pii.map((p) => ({
            ...p,
            type: "dw_column",
            label: "x",
            layer: "core",
            pii_note: null,
          })),
          tables: [],
          source_columns: [],
          kpis: [],
        });
      case `GET ${TABLES}`:
        return json({
          items: tables.filter((t) => t.layer === r.query.layer).map(summary),
        });
    }
    const found = tables.find((t) => r.path === `${TABLES}/${t.id}`);
    return found && r.method === "GET" ? json(found) : undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the star diagram", () => {
  it("draws facts and dimensions with the FK role names", async () => {
    renderApp(backend([DATE, CUSTOMER, PRODUCT, SALES]), `${PATH}?folder=dw/core/model`);

    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(within(diagram).getByText("fact_sales")).toBeInTheDocument();
    expect(within(diagram).getByText("dim_date")).toBeInTheDocument();
    expect(within(diagram).getByText("dim_customer")).toBeInTheDocument();

    const links = screen.getByRole("list", { name: "Relationships" });
    expect(within(links).getByText("fact_sales → dim_date (order date)")).toBeInTheDocument();
    expect(within(links).getByText("fact_sales → dim_date (ship date)")).toBeInTheDocument();
    // Without a role name the column stands in.
    expect(within(links).getByText("fact_sales → dim_customer (customer_key)")).toBeInTheDocument();
  });

  it("marks key columns and sits in the themed canvas", async () => {
    const keyed = table(2, "dim_customer", {
      is_conformed: true,
      columns: [
        column(10, id(2), "customer_sk", { role: "sk" }),
        column(11, id(2), "customer_id", { role: "nk" }),
      ],
    });
    renderApp(backend([DATE, keyed, SALES]), `${PATH}?folder=dw/core/model`);

    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(diagram).toHaveClass("dawam-flow");
    expect(diagram.querySelectorAll("svg.star-key")).toHaveLength(1);
    expect(within(diagram).getByText(/customer_sk/)).toBeInTheDocument();
  });

  it("marks PII-derived columns, with a label and in the list", async () => {
    const customer = table(2, "dim_customer", {
      is_conformed: true,
      columns: [
        column(10, id(2), "customer_sk", { role: "sk" }),
        column(12, id(2), "email", { role: "attribute" }),
        column(13, id(2), "phone", { role: "attribute" }),
      ],
    });
    const pii = [
      { id: customer.columns[1]!.id, pseudonymised: false },
      { id: customer.columns[2]!.id, pseudonymised: true },
    ];
    renderApp(backend([DATE, customer, SALES], pii), `${PATH}?folder=dw/core/model`);

    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(await within(diagram).findByRole("img", { name: "PII-derived" })).toBeInTheDocument();
    expect(within(diagram).getByRole("img", { name: "PII-derived, pseudonymised" })).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "PII in the model" });
    expect(within(list).getByText("dim_customer.email (PII-derived)")).toBeInTheDocument();
    expect(within(list).getByText("dim_customer.phone (PII-derived, pseudonymised)")).toBeInTheDocument();
  });

  it("marks a PII-influenced table and counts its PII-derived columns", async () => {
    const customer = table(2, "dim_customer", {
      is_conformed: true,
      columns: [column(12, id(2), "email", {}), column(13, id(2), "phone", {})],
    });
    const spy = (r: ApiRequest) =>
      r.path === `/api/v1/workspaces/${WORKSPACE_ID}/data-warehouse/pii`
        ? json({
            columns: customer.columns.map((c) => ({
              id: c.id, type: "dw_column", label: "x", layer: "core", pseudonymised: false, pii_note: null,
            })),
            tables: [
              { id: DATE.id, type: "dw_table", label: "dim_date", layer: "core" },
            ],
            source_columns: [],
            kpis: [],
          })
        : backend([DATE, customer, SALES])(r);
    renderApp(spy, `${PATH}?folder=dw/core/model`);

    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(
      await within(diagram).findByRole("img", { name: "2 PII-derived columns: email, phone" }),
    ).toBeInTheDocument();
    expect(within(diagram).getByRole("img", { name: "PII-influenced table" })).toBeInTheDocument();
    expect(screen.getByText("dim_date (PII-influenced)")).toBeInTheDocument();
  });

  it("puts the key icon on surrogate keys only", async () => {
    const keyed = table(2, "dim_customer", {
      is_conformed: true,
      columns: [column(10, id(2), "customer_sk", { role: "sk" }), column(11, id(2), "customer_id", { role: "nk" })],
    });
    renderApp(backend([DATE, keyed, SALES]), `${PATH}?folder=dw/core/model`);
    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(diagram.querySelectorAll("svg.star-key")).toHaveLength(1);
  });

  it("draws no PII marker when nothing is PII-derived", async () => {
    renderApp(backend([DATE, CUSTOMER, SALES]), `${PATH}?folder=dw/core/model`);

    const diagram = await screen.findByRole("group", { name: "Star diagram" });
    expect(within(diagram).queryByRole("img", { name: /PII/ })).not.toBeInTheDocument();
  });

  it("includes the Core conformed dimensions a Mart fact points at", async () => {
    const mart = { ...SALES, layer: "mart" as const };
    renderApp(backend([DATE, CUSTOMER, PRODUCT, mart]), `${PATH}?folder=dw/mart/model`);

    const links = await screen.findByRole("list", { name: "Relationships" });
    expect(within(links).getByText("fact_sales → dim_date (order date)")).toBeInTheDocument();
    // dim_product is not conformed, so a Mart cannot see it.
    expect(within(links).queryByText(/dim_product/)).not.toBeInTheDocument();
  });

  it("has no diagram without tables, nor in Staging", async () => {
    renderApp(backend([]), `${PATH}?folder=dw/core/model`);

    expect(await screen.findByText(/No tables in the Core Layer yet/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Star diagram" })).not.toBeInTheDocument();
  });
});

describe("the bus matrix", () => {
  it("crosses facts with conformed dimensions, naming the roles", async () => {
    renderApp(backend([DATE, CUSTOMER, PRODUCT, SALES, RETURNS]), `${PATH}?folder=dw/core/model`);

    const matrix = await screen.findByRole("table", { name: "Bus matrix" });
    expect(
      within(matrix)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Fact", "dim_customer", "dim_date"]);

    const sales = within(matrix).getByRole("row", { name: /fact_sales/ });
    expect(sales).toHaveTextContent("order date, ship date");
    expect(sales).toHaveTextContent("●");
    const returns = within(matrix).getByRole("row", { name: /fact_returns/ });
    expect(
      within(returns)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    ).toEqual(["● customer_key", ""]);
  });

  it("explains itself when there is nothing to cross", async () => {
    renderApp(backend([SALES]), `${PATH}?folder=dw/core/model`);

    expect(
      await screen.findByText(/needs at least one fact and one conformed dimension/),
    ).toBeInTheDocument();
  });
});
