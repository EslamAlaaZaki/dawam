import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwColumn, DwTable, DwTableSummary } from "../api/dwModel";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  apiError,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

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

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: role === "viewer" ? ["workspace.view"] : ["dw_schema.edit", "workspace.view"],
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const KEY: DwColumn = {
  id: "c0000000-0000-4000-8000-000000000001",
  table_id: "a0000000-0000-4000-8000-000000000002",
  name: "dim_customer_key",
  ordinal: 1,
  data_type: { type: "bigint", length: null, precision: null, scale: null },
  is_nullable: false,
  role: "sk",
  additivity: null,
  scd_type_override: null,
  references_table_id: null,
  role_name: null,
  description: "",
  semantic_type: null,
  is_system: false,
  version: 1,
};

const HASH: DwColumn = {
  ...KEY,
  id: "c0000000-0000-4000-8000-000000000002",
  name: "row_hash",
  ordinal: 2,
  data_type: { type: "string", length: 64, precision: null, scale: null },
  role: "row_hash",
  is_system: true,
};

const CUSTOMER: DwTable = {
  id: "a0000000-0000-4000-8000-000000000002",
  layer: "core",
  name: "dim_customer",
  kind: "dimension",
  fact_type: null,
  grain: null,
  is_aggregate: false,
  scd_type: 2,
  is_conformed: true,
  unknown_member: { surrogate_key: -1, defaults: {} },
  description: "",
  columns: [KEY, HASH],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
  version: 1,
};

const SALES: DwTable = {
  ...CUSTOMER,
  id: "a0000000-0000-4000-8000-000000000001",
  name: "fact_sales",
  kind: "fact",
  fact_type: "transactional",
  grain: "One row per order line",
  scd_type: null,
  is_conformed: false,
  unknown_member: null,
  columns: [],
};

function summary(table: DwTable): DwTableSummary {
  const { columns, unknown_member, created_at, updated_at, ...rest } = table;
  void unknown_member;
  void created_at;
  void updated_at;
  return { ...rest, column_count: columns.length } as DwTableSummary;
}

function backend(
  role: WorkspaceRole,
  tables: DwTable[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`:
        return json({ set_up: true, version: 1 });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET ${TABLES}`:
        return (
          handle(r) ??
          json({
            items: tables.filter((t) => t.layer === r.query.layer).map(summary),
          })
        );
    }
    const table = tables.find((t) => r.path === `${TABLES}/${t.id}`);
    if (table && r.method === "GET") {
      return json(table);
    }
    return handle(r);
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the model folders", () => {
  it("lists a Layer's tables read-only for a viewer", async () => {
    renderApp(backend("viewer", [CUSTOMER, SALES]), `${PATH}?folder=dw/core/model`);

    const fact = await screen.findByRole("row", { name: /fact_sales/ });
    expect(fact).toHaveTextContent("Fact");
    expect(fact).toHaveTextContent("One row per order line");
    // The bus matrix below also has a row (its header) naming dim_customer.
    expect(screen.getByRole("button", { name: "dim_customer" }).closest("tr")).toHaveTextContent(
      "SCD 2",
    );
    expect(screen.queryByRole("form", { name: "Add table" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and editors can edit the model.")).toBeInTheDocument();
  });

  it("asks only for the Layer it is in", async () => {
    const requests = renderApp(
      backend("viewer", [{ ...SALES, layer: "mart" }]),
      `${PATH}?folder=dw/mart/model`,
    );

    expect(await screen.findByRole("row", { name: /fact_sales/ })).toBeInTheDocument();
    expect(requests.some((r) => r.path === TABLES && r.query.layer === "mart")).toBe(true);
  });

  it("shows an empty state", async () => {
    renderApp(backend("viewer", []), `${PATH}?folder=dw/core/model`);

    expect(await screen.findByText(/No tables in the Core Layer yet/)).toBeInTheDocument();
  });

  it("shows a table's columns, marking the ones DAWAM maintains", async () => {
    renderApp(backend("viewer", [CUSTOMER]), `${PATH}?folder=dw/core/model`);

    fireEvent.click(await screen.findByRole("button", { name: "dim_customer" }));

    const details = await screen.findByRole("region", { name: "Table dim_customer" });
    expect(within(details).getByText(/Unknown member/)).toHaveTextContent("-1");
    expect(within(details).getByRole("row", { name: /dim_customer_key/ })).toHaveTextContent(
      "Surrogate key",
    );
    const hash = within(details).getByRole("row", { name: /row_hash/ });
    expect(hash).toHaveTextContent("DAWAM");
    expect(within(hash).queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
  });
});

describe("adding a table", () => {
  it("lets an editor add a fact with its grain and fact type", async () => {
    const tables: DwTable[] = [];
    const requests = renderApp(
      backend("editor", tables, (r) => {
        if (r.method === "POST" && r.path === TABLES) {
          tables.push(SALES);
          return json(SALES, 201);
        }
      }),
      `${PATH}?folder=dw/core/model`,
    );

    const form = await screen.findByRole("form", { name: "Add table" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "fact_sales" } });
    fireEvent.change(within(form).getByLabelText("Kind"), { target: { value: "fact" } });
    fireEvent.change(within(form).getByLabelText("Grain"), {
      target: { value: "One row per order line" },
    });
    fireEvent.change(within(form).getByLabelText("Fact type"), {
      target: { value: "transactional" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add table" }));

    await screen.findByRole("region", { name: "Table fact_sales" });
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      layer: "core",
      name: "fact_sales",
      kind: "fact",
      grain: "One row per order line",
      fact_type: "transactional",
    });
  });

  it("adds a dimension with an SCD type, conformed", async () => {
    const requests = renderApp(
      backend("editor", [], (r) =>
        r.method === "POST" && r.path === TABLES ? json(CUSTOMER, 201) : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    const form = await screen.findByRole("form", { name: "Add table" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "dim_customer" } });
    fireEvent.change(within(form).getByLabelText("Kind"), { target: { value: "dimension" } });
    expect(within(form).queryByLabelText("Grain")).not.toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("SCD type"), { target: { value: "2" } });
    fireEvent.click(within(form).getByLabelText("Conformed dimension"));
    fireEvent.click(within(form).getByRole("button", { name: "Add table" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      layer: "core",
      name: "dim_customer",
      kind: "dimension",
      scd_type: 2,
      is_conformed: true,
    });
  });

  it("shows the server's reason when it refuses", async () => {
    renderApp(
      backend("editor", [], (r) =>
        r.method === "POST" && r.path === TABLES
          ? apiError(409, "name_taken", "Another table in this Layer already has this name.")
          : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    const form = await screen.findByRole("form", { name: "Add table" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "dim_customer" } });
    fireEvent.change(within(form).getByLabelText("Kind"), { target: { value: "dimension" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add table" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("already has this name");
  });
});

describe("editing a table", () => {
  it("adds a measure with its additivity", async () => {
    const requests = renderApp(
      backend("editor", [SALES], (r) =>
        r.method === "POST" && r.path === `${TABLES}/${SALES.id}/columns`
          ? json({ ...KEY, name: "amount" }, 201)
          : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    fireEvent.click(await screen.findByRole("button", { name: "fact_sales" }));
    const form = await screen.findByRole("form", { name: "Add column" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "amount" } });
    fireEvent.change(within(form).getByLabelText("Data type"), { target: { value: "decimal" } });
    fireEvent.change(within(form).getByLabelText("Precision"), { target: { value: "18" } });
    fireEvent.change(within(form).getByLabelText("Scale"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "measure" } });
    fireEvent.change(within(form).getByLabelText("Additivity"), {
      target: { value: "additive" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add column" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      name: "amount",
      data_type: { type: "decimal", precision: 18, scale: 2 },
      role: "measure",
      additivity: "additive",
      is_nullable: true,
    });
  });

  it("adds a foreign key to a dimension with a role name", async () => {
    const requests = renderApp(
      backend("editor", [SALES, CUSTOMER], (r) =>
        r.method === "POST" && r.path === `${TABLES}/${SALES.id}/columns`
          ? json(KEY, 201)
          : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    fireEvent.click(await screen.findByRole("button", { name: "fact_sales" }));
    const form = await screen.findByRole("form", { name: "Add column" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "order_date_key" } });
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "fk" } });
    fireEvent.change(within(form).getByLabelText("References"), {
      target: { value: CUSTOMER.id },
    });
    fireEvent.change(within(form).getByLabelText("Role name"), { target: { value: "order_date" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add column" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toMatchObject({
      name: "order_date_key",
      role: "fk",
      references_table_id: CUSTOMER.id,
      role_name: "order_date",
    });
  });

  it("deletes a column and a table", async () => {
    const requests = renderApp(
      backend("editor", [CUSTOMER], (r) =>
        r.method === "DELETE" ? noContent() : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    fireEvent.click(await screen.findByRole("button", { name: "dim_customer" }));
    const details = await screen.findByRole("region", { name: "Table dim_customer" });
    fireEvent.click(within(details).getByRole("button", { name: "Delete dim_customer_key" }));
    await waitFor(() =>
      expect(requests.find((r) => r.method === "DELETE")?.path).toBe(
        `${TABLES}/${CUSTOMER.id}/columns/${KEY.id}`,
      ),
    );

    fireEvent.click(within(details).getByRole("button", { name: "Delete table" }));
    await waitFor(() =>
      expect(requests.filter((r) => r.method === "DELETE").map((r) => r.path)).toContain(
        `${TABLES}/${CUSTOMER.id}`,
      ),
    );
  });

  it("saves a table's changes with its version", async () => {
    const requests = renderApp(
      backend("editor", [SALES], (r) =>
        r.method === "PATCH" ? json({ ...SALES, description: "Sales", version: 2 }) : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    fireEvent.click(await screen.findByRole("button", { name: "fact_sales" }));
    const form = await screen.findByRole("form", { name: "Edit fact_sales" });
    fireEvent.change(within(form).getByLabelText("Description"), { target: { value: "Sales" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `${TABLES}/${SALES.id}`,
      body: {
        version: 1,
        name: "fact_sales",
        description: "Sales",
        grain: "One row per order line",
        fact_type: "transactional",
      },
    });
  });

  it("offers a reload when someone else changed the table", async () => {
    renderApp(
      backend("editor", [SALES], (r) =>
        r.method === "PATCH"
          ? apiError(409, "version_conflict", "Someone else changed this.")
          : undefined,
      ),
      `${PATH}?folder=dw/core/model`,
    );

    fireEvent.click(await screen.findByRole("button", { name: "fact_sales" }));
    const form = await screen.findByRole("form", { name: "Edit fact_sales" });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("Someone else changed");
    expect(within(form).getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
