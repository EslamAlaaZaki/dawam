import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { SchemaSearchHit, SnapshotSummary, SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const VIEWER: Workspace = {
  id: WORKSPACE_ID,
  name: "Retail DW",
  description: "",
  domain: "",
  role: "viewer",
  permissions: ["job.cancel_own", "workspace.view"],
  status: "active",
  archived_at: null,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const CBS: SourceSystem = {
  id: "33333333-3333-4333-8333-333333333333",
  workspace_id: WORKSPACE_ID,
  name: "Core Banking",
  code: "cbs",
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};
const SYSTEM = `${API}/systems/${CBS.id}`;
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CBS.id}/source-schema`;

const SUMMARY: SnapshotSummary = {
  id: "55555555-5555-4555-8555-555555555555",
  source_system_id: CBS.id,
  origin: "connection",
  job_id: null,
  taken_at: "2026-01-05T10:00:00Z",
  is_latest: true,
  schema_count: 1,
  table_count: 2,
  column_count: 3,
  routine_count: 1,
};

const SCHEMA: SourceSchema = {
  ...SUMMARY,
  db_schemas: [{ id: "a1", name: "core", status: "present" }],
  tables: [
    {
      id: "t1",
      db_schema: "core",
      name: "customers",
      kind: "table",
      status: "present",
      view_definition: null,
      row_estimate: 1200,
      comment: "One row per bank customer",
      columns: [
        {
          id: "c1",
          name: "cust_no",
          status: "present",
          ordinal: 1,
          data_type: "integer",
          is_nullable: false,
          is_pk: true,
          default: null,
          comment: null,
        },
      ],
      constraints: [
        {
          name: "customers_pkey",
          type: "pk",
          columns: ["cust_no"],
          ref_table_id: null,
          ref_db_schema: null,
          ref_table: null,
          ref_columns: [],
        },
      ],
      indexes: [
        {
          name: "customers_lower_idx",
          columns: ["lower(email)"],
          is_unique: false,
        },
      ],
    },
    {
      id: "t2",
      db_schema: "core",
      name: "customer_balances",
      kind: "view",
      status: "present",
      view_definition: "SELECT cust_no FROM core.customers",
      row_estimate: null,
      comment: null,
      columns: [],
      constraints: [],
      indexes: [],
    },
  ],
  routines: [
    {
      id: "r1",
      db_schema: "core",
      name: "account_turnover",
      kind: "function",
      status: "present",
      signature: "p_acct integer",
      definition: "SELECT sum(amount) FROM core.transactions",
    },
  ],
  removed_columns: [
    { id: "c2", table_id: "t1", name: "legacy_code", status: "source_removed", data_type: "text" },
  ],
  removed_tables: [
    {
      id: "t3",
      db_schema: "core",
      name: "old_ledger",
      kind: "table",
      status: "source_removed",
    },
  ],
};

const HITS: SchemaSearchHit[] = [
  {
    kind: "column",
    id: "c1",
    name: "cust_no",
    db_schema: "core",
    table: "customers",
    status: "present",
  },
];

function backend(searches: string[]) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(VIEWER);
      case `GET ${API}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${SYSTEM}/snapshots`:
        return json({ items: [SUMMARY] });
      case `GET ${SYSTEM}/schema`:
        return json(SCHEMA);
      case `GET ${SYSTEM}/schema/search`:
        searches.push(r.query.q ?? "");
        return json({ items: HITS });
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Source Schema browser", () => {
  it("shows Database Schemas with their tables, views and routines, and flags removed objects", async () => {
    renderApp(backend([]), FOLDER);

    const tables = await screen.findByRole("list", {
      name: "Tables and views of core",
    });
    expect(within(tables).getByText("customers")).toBeInTheDocument();
    expect(within(tables).getByText("customer_balances").closest("li")).toHaveTextContent("(view)");
    expect(within(tables).getByText("old_ledger").closest("li")).toHaveTextContent(
      "removed from the source",
    );
    const routines = screen.getByRole("list", { name: "Routines of core" });
    expect(routines).toHaveTextContent("account_turnover(p_acct integer) (function)");
  });

  it("opens a table with its columns, keys, indexes, row estimate and comment", async () => {
    renderApp(backend([]), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "customers" }));

    const page = await screen.findByRole("region", {
      name: "Table core.customers",
    });
    expect(page).toHaveTextContent("1200");
    expect(page).toHaveTextContent("One row per bank customer");
    const row = within(page).getByText("legacy_code").closest("tr");
    expect(row).toHaveTextContent("removed from the source");
    expect(within(page).getByText("cust_no").closest("tr")).toHaveTextContent("PK");
    expect(within(page).getByRole("list", { name: "Keys" })).toHaveTextContent(
      "customers_pkey: PK (cust_no)",
    );
    expect(within(page).getByRole("list", { name: "Indexes" })).toHaveTextContent(
      "customers_lower_idx (lower(email))",
    );
  });

  it("shows a view's definition and a routine's code", async () => {
    renderApp(backend([]), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "customer_balances" }));
    expect(await screen.findByText("SELECT cust_no FROM core.customers")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /account_turnover/ }));
    expect(
      await screen.findByText("SELECT sum(amount) FROM core.transactions"),
    ).toBeInTheDocument();
  });

  it("searches by name and opens a result's table", async () => {
    const searches: string[] = [];
    renderApp(backend(searches), FOLDER);

    fireEvent.change(await screen.findByLabelText("Search by name"), {
      target: { value: "cust_no" },
    });

    const results = await screen.findByRole("list", { name: "Search results" });
    expect(searches).toEqual(["cust_no"]);
    expect(results).toHaveTextContent("core.customers.cust_no (column)");
    fireEvent.click(within(results).getByRole("button"));
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Table core.customers" })).toBeInTheDocument(),
    );
  });
});
