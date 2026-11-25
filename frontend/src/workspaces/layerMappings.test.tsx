import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { openMenu } from "../test/menus";
import { currentLocation } from "../test/navigation";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const DW = `${API}/data-warehouse`;
const PATH = `/workspaces/${WORKSPACE_ID}`;

const ADA = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(role: "viewer" | "editor") {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: role === "viewer" ? ["workspace.view"] : ["dw_schema.edit", "workspace.view"],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const CUSTOMER = "a0000000-0000-4000-8000-000000000001";
const SALES = "a0000000-0000-4000-8000-000000000002";
const DATE = "a0000000-0000-4000-8000-000000000003";
const BRANCH_A = "b0000000-0000-4000-8000-00000000000a";
const BRANCH_B = "b0000000-0000-4000-8000-00000000000b";
const COL_NAME = "c0000000-0000-4000-8000-000000000001";
const COL_EMAIL = "c0000000-0000-4000-8000-000000000002";
const COL_LOADED = "c0000000-0000-4000-8000-000000000003";

function summary(id: string, name: string, kind: string, extra = {}) {
  return {
    id,
    layer: "core",
    name,
    kind,
    fact_type: null,
    grain: null,
    is_aggregate: false,
    scd_type: null,
    is_conformed: false,
    description: "",
    column_count: 3,
    version: 1,
    naming_violation_count: 0,
    review_flag_count: 0,
    status: "present",
    ...extra,
  };
}

const COVERAGE = {
  layers: [
    {
      layer: "core",
      tables: [
        { table_id: CUSTOMER, table_name: "dim_customer", layer: "core", coverage: { total: 2, covered: 1, percent: 50 } },
        { table_id: SALES, table_name: "fact_sales", layer: "core", coverage: { total: 4, covered: 4, percent: 100 } },
        { table_id: DATE, table_name: "dim_date", layer: "core", coverage: { total: 0, covered: 0, percent: 0 } },
      ],
      coverage: { total: 6, covered: 5, percent: 83.3 },
    },
    { layer: "mart", tables: [], coverage: { total: 0, covered: 0, percent: 0 } },
  ],
  coverage: { total: 6, covered: 5, percent: 83.3 },
};

function col(id: string, name: string, type: string, version: number, sql = "", extra = {}) {
  return {
    id: type === "unmapped" ? null : `m-${id}`,
    column_id: id,
    column_name: name,
    mapping_type: type,
    rule_text: "",
    sql_expression: sql,
    inputs: [],
    uses: [],
    validation: { unparsed: false, errors: [] },
    version,
    ...extra,
  };
}

function mapping(tableId: string, name: string, extra = {}) {
  return {
    table_id: tableId,
    table_name: name,
    layer: "core",
    source_layer: "staging",
    integration_rule: null,
    match_keys: [],
    notes: "",
    version: 1,
    columns: [],
    is_aggregate: false,
    branches: [],
    coverage: [],
    sql: null,
    ...extra,
  };
}

// A table without branches: one unmapped column, one system column.
const CUSTOMER_MAPPING = mapping(CUSTOMER, "dim_customer", {
  columns: [
    col(COL_NAME, "full_name", "unmapped", 0),
    col(COL_EMAIL, "email", "direct", 2, "stg_cbs_customers.email", {
      validation: {
        unparsed: false,
        errors: [{ code: "type_truncation", message: "varchar(80) is cut to varchar(40)" }],
      },
    }),
    col(COL_LOADED, "_loaded_at", "system", 1),
  ],
});

// A table with two branches; the second one has an unmapped column.
const SALES_MAPPING = mapping(SALES, "fact_sales", {
  integration_rule: "Prefer the Core Banking value",
  match_keys: ["customer_nk"],
  branches: [
    {
      id: BRANCH_A,
      ordinal: 1,
      name: "Core Banking",
      driving_input: "stg_cbs_sales s",
      joins: "JOIN stg_cbs_customers c ON c.id = s.customer_id",
      filters: "s.deleted = 0",
      group_by: null,
      having: null,
      version: 1,
      columns: [col(COL_NAME, "amount", "direct", 1, "s.amount")],
      errors: [],
    },
    {
      id: BRANCH_B,
      ordinal: 2,
      name: "Web Shop",
      driving_input: "stg_web_orders o",
      joins: "",
      filters: "",
      group_by: null,
      having: null,
      version: 1,
      columns: [col(COL_NAME, "amount", "unmapped", 0)],
      errors: [],
    },
  ],
});

const DETAIL = (id: string, name: string) => ({
  ...summary(id, name, "dimension"),
  columns: [
    {
      id: COL_NAME,
      table_id: id,
      name: "full_name",
      ordinal: 1,
      data_type: { type: "string", length: 80 },
      is_nullable: true,
      role: "attribute",
      is_system: false,
      version: 1,
      naming_violations: [],
      review_flags: [],
      status: "present",
      pii_note: "Name of a person",
    },
  ],
  naming_violations: [],
});

interface Options {
  role?: "viewer" | "editor";
  saveBranch?: (r: ApiRequest) => Response;
  saveTable?: (r: ApiRequest) => Response;
  tableMapping?: () => Response;
}

function backend(options: Options = {}) {
  return (r: ApiRequest) => {
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(options.role ?? "viewer"));
      case `GET ${API}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${DW}`:
        return json({ set_up: true, target_platform: "postgresql", version: 1 });
      case `GET ${API}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET ${DW}/coverage`:
        return json(COVERAGE);
      case `GET ${DW}/tables`:
        return json({
          items: [
            summary(CUSTOMER, "dim_customer", "dimension", { scd_type: 2, is_conformed: true }),
            summary(SALES, "fact_sales", "fact"),
            summary(DATE, "dim_date", "generated"),
          ],
        });
      case `GET ${DW}/tables/${CUSTOMER}`:
        return json(DETAIL(CUSTOMER, "dim_customer"));
      case `GET ${DW}/tables/${SALES}`:
        return json(DETAIL(SALES, "fact_sales"));
      case `GET ${DW}/tables/${CUSTOMER}/mapping`:
        return options.tableMapping?.() ?? json(CUSTOMER_MAPPING);
      case `GET ${DW}/tables/${SALES}/mapping`:
        return json(SALES_MAPPING);
      case `PUT ${DW}/tables/${CUSTOMER}/mapping/columns/${COL_NAME}`:
        return options.saveTable?.(r);
      case `PUT ${DW}/tables/${SALES}/mapping/branches/${BRANCH_B}/columns/${COL_NAME}`:
        return options.saveBranch?.(r);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

const core = (extra = "") => `${PATH}?folder=dw/core/mappings${extra}`;

describe("the Core and Mart Mappings page", () => {
  it("shows the Layer's coverage and its tables", async () => {
    renderApp(backend(), core());

    expect(await screen.findByRole("heading", { name: /Core mappings/ })).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    const coverage = screen.getByRole("region", { name: "Coverage" });
    expect(within(coverage).getByText("83%")).toBeInTheDocument();
    expect(within(coverage).getByText("5 of 6 columns mapped")).toBeInTheDocument();
    expect(within(coverage).getByText("1 fully mapped")).toBeInTheDocument();

    const list = screen.getByRole("navigation", { name: "Core tables" });
    const customer = within(list).getByRole("button", { name: /dim_customer/ });
    expect(customer).toHaveTextContent("50%");
    expect(customer).toHaveAttribute("aria-current", "true");
    expect(within(list).getByRole("button", { name: /fact_sales/ })).toHaveTextContent("100%");
    expect(within(list).getByRole("button", { name: /dim_date/ })).toHaveTextContent(
      "no mapping needed",
    );
  });

  it("selects a table and shows its columns, validation and the unmapped state", async () => {
    renderApp(backend(), core());

    const region = await screen.findByRole("region", { name: "Mappings of dim_customer" });
    expect(await within(region).findByText("1 of 2 columns covered")).toBeInTheDocument();
    const rows = await within(region).findByRole("list", { name: "Column mappings" });
    expect(within(rows).getByText("Not mapped yet")).toBeInTheDocument();
    expect(within(rows).getByText("stg_cbs_customers.email")).toBeInTheDocument();
    expect(within(rows).getByText("varchar(80) is cut to varchar(40)")).toBeInTheDocument();
    expect(within(rows).getByText("Unmapped")).toBeInTheDocument();
    // The type and the PII badge come from the table's column.
    expect(await within(rows).findByText("PII")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /fact_sales/ }));
    expect(
      await screen.findByRole("region", { name: "Mappings of fact_sales" }),
    ).toBeInTheDocument();
  });

  it("preselects the table named in the URL", async () => {
    renderApp(backend(), core(`&table=${SALES}`));

    expect(
      await screen.findByRole("region", { name: "Mappings of fact_sales" }),
    ).toBeInTheDocument();
  });

  it("shows a branch tab per branch with its read-only SQL, and the integration rule", async () => {
    renderApp(backend(), core(`&table=${SALES}`));

    const region = await screen.findByRole("region", { name: "Mappings of fact_sales" });
    expect(await within(region).findByText("Prefer the Core Banking value")).toBeInTheDocument();
    expect(within(region).getByText("customer_nk")).toBeInTheDocument();
    expect(within(region).getByText("stg_cbs_sales s")).toBeInTheDocument();
    expect(within(region).getByText("s.deleted = 0")).toBeInTheDocument();
    expect(within(region).getByText("s.amount")).toBeInTheDocument();

    fireEvent.mouseDown(within(region).getByRole("tab", { name: "Web Shop" }), { button: 0 });
    expect(await within(region).findByText("stg_web_orders o")).toBeInTheDocument();
    expect(within(region).getByText("Not mapped yet")).toBeInTheDocument();
  });

  it("shows a generated table as needing no mapping", async () => {
    renderApp(backend(), core(`&table=${DATE}`));

    expect(await screen.findByText("Generated table, no mapping needed")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Column mappings" })).not.toBeInTheDocument();
  });

  it("keeps rows read-only for a viewer", async () => {
    renderApp(backend(), core());

    const rows = await screen.findByRole("list", { name: "Column mappings" });
    expect(within(rows).queryByRole("button")).not.toBeInTheDocument();
  });

  it("opens one row at a time for an editor, never a system column, and cancels", async () => {
    renderApp(backend({ role: "editor" }), core());

    const rows = await screen.findByRole("list", { name: "Column mappings" });
    expect(within(rows).queryByRole("button", { name: /_loaded_at/ })).not.toBeInTheDocument();

    fireEvent.click(within(rows).getByRole("button", { name: "Edit mapping of full_name" }));
    expect(screen.getByRole("form", { name: "Mapping of full_name" })).toBeInTheDocument();
    fireEvent.click(within(rows).getByRole("button", { name: "Edit mapping of email" }));
    expect(screen.queryByRole("form", { name: "Mapping of full_name" })).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Mapping of email" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("saves a table-level mapping through the table column endpoint", async () => {
    const requests = renderApp(
      backend({ role: "editor", saveTable: () => json(CUSTOMER_MAPPING) }),
      core(),
    );
    const rows = await screen.findByRole("list", { name: "Column mappings" });

    fireEvent.click(within(rows).getByRole("button", { name: "Edit mapping of full_name" }));
    const form = screen.getByRole("form", { name: "Mapping of full_name" });
    fireEvent.change(within(form).getByLabelText("Mapping type"), { target: { value: "direct" } });
    fireEvent.change(within(form).getByLabelText("SQL expression"), {
      target: { value: "stg_cbs_customers.name" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save mapping" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      mapping_type: "direct",
      rule_text: "",
      sql_expression: "stg_cbs_customers.name",
      version: 0,
    });
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
  });

  it("saves a branch mapping through the branch column endpoint", async () => {
    const requests = renderApp(
      backend({ role: "editor", saveBranch: () => json(SALES_MAPPING) }),
      core(`&table=${SALES}`),
    );
    const region = await screen.findByRole("region", { name: "Mappings of fact_sales" });
    fireEvent.mouseDown(await within(region).findByRole("tab", { name: "Web Shop" }), {
      button: 0,
    });

    fireEvent.click(await within(region).findByRole("button", { name: "Edit mapping of amount" }));
    const form = screen.getByRole("form", { name: "Mapping of amount" });
    // A branch can mark a column NULL.
    expect(within(form).getByRole("option", { name: "Not in this branch (NULL)" })).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("Mapping type"), { target: { value: "direct" } });
    fireEvent.change(within(form).getByLabelText("SQL expression"), {
      target: { value: "o.total" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save mapping" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    expect(requests.find((r) => r.method === "PUT")?.path).toContain(`/branches/${BRANCH_B}/`);
    expect(requests.find((r) => r.method === "PUT")?.body).toMatchObject({
      mapping_type: "direct",
      sql_expression: "o.total",
      version: 0,
    });
  });

  it("shows a save error in the row and refetches on a version conflict, keeping the row open", async () => {
    const requests = renderApp(
      backend({
        role: "editor",
        saveTable: () => apiError(409, "version_conflict", "Someone else changed this."),
      }),
      core(),
    );
    const rows = await screen.findByRole("list", { name: "Column mappings" });
    fireEvent.click(within(rows).getByRole("button", { name: "Edit mapping of full_name" }));
    const form = screen.getByRole("form", { name: "Mapping of full_name" });
    fireEvent.click(within(form).getByRole("button", { name: "Save mapping" }));

    expect(await within(form).findByText("Someone else changed this.")).toBeInTheDocument();
    const mappingGets = () =>
      requests.filter((r) => r.method === "GET" && r.path === `${DW}/tables/${CUSTOMER}/mapping`);
    await waitFor(() => expect(mappingGets().length).toBeGreaterThan(1));
    expect(screen.getByRole("form", { name: "Mapping of full_name" })).toBeInTheDocument();
  });

  it("offers XLSX and CSV downloads for the Layer", async () => {
    renderApp(backend(), core());

    openMenu(await screen.findByRole("button", { name: /Export mapping sheet/ }));
    expect(await screen.findByRole("menuitem", { name: /Download XLSX/ })).toHaveAttribute(
      "href",
      `${DW}/mapping-sheet?layer=core&format=xlsx`,
    );
    expect(screen.getByRole("menuitem", { name: /Download CSV/ })).toHaveAttribute(
      "href",
      `${DW}/mapping-sheet?layer=core&format=csv`,
    );
  });

  it("uses the Mart wording and Layer for the Mart page", async () => {
    renderApp(backend(), `${PATH}?folder=dw/mart/mappings`);

    expect(await screen.findByRole("heading", { name: /Mart mappings/ })).toBeInTheDocument();
    expect(screen.getByText(/How every Mart column is built from the Core Layer/)).toBeInTheDocument();
  });

  it("links from the Model page to the Mappings page with the table selected", async () => {
    renderApp(backend(), `${PATH}?folder=dw/core/model`);

    fireEvent.click(await screen.findByRole("button", { name: "fact_sales" }));
    expect(screen.queryByRole("button", { name: "Mappings" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("link", { name: "Open in Mappings" }));

    expect(currentLocation()).toBe(
      `${PATH}?${new URLSearchParams({ folder: "dw/core/mappings", table: SALES })}`,
    );
    expect(
      await screen.findByRole("region", { name: "Mappings of fact_sales" }),
    ).toBeInTheDocument();
  });
});
