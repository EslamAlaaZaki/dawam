import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { Relationship } from "../api/relationships";
import type { SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import {
  apiError,
  CSRF_TOKEN,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const WORKSPACE: Workspace = {
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
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CBS.id}/er-diagram`;

function table(
  id: string,
  db_schema: string,
  name: string,
  fks: { ref: string; ref_schema: string; ref_table: string }[] = [],
): SourceSchema["tables"][number] {
  return {
    id,
    db_schema,
    name,
    kind: "table",
    status: "present",
    view_definition: null,
    row_estimate: null,
    comment: null,
    columns: [],
    indexes: [],
    constraints: fks.map((fk) => ({
      name: `${name}_${fk.ref_table}_fk`,
      type: "fk" as const,
      columns: [`${fk.ref_table}_id`],
      ref_table_id: fk.ref,
      ref_db_schema: fk.ref_schema,
      ref_table: fk.ref_table,
      ref_columns: ["id"],
    })),
  };
}

// accounts -> customers is declared; transactions -> accounts is accepted-inferred;
// audit.events stands alone in another Database Schema.
const SCHEMA = {
  id: "55555555-5555-4555-8555-555555555555",
  source_system_id: CBS.id,
  origin: "connection",
  job_id: null,
  taken_at: "2026-01-05T10:00:00Z",
  is_latest: true,
  schema_count: 2,
  table_count: 4,
  column_count: 0,
  routine_count: 0,
  db_schemas: [
    { id: "s1", name: "core", status: "present" },
    { id: "s2", name: "audit", status: "present" },
  ],
  tables: [
    table("t-cust", "core", "customers"),
    table("t-acct", "core", "accounts", [
      { ref: "t-cust", ref_schema: "core", ref_table: "customers" },
    ]),
    table("t-tx", "core", "transactions"),
    table("t-evt", "audit", "events"),
  ],
  routines: [],
  removed_columns: [],
  removed_tables: [],
} as unknown as SourceSchema;

const INFERRED: Relationship = {
  id: "66666666-6666-4666-8666-666666666666",
  from_column: {
    column_id: "c1",
    table_id: "t-tx",
    db_schema: "core",
    table: "transactions",
    column: "acct_no",
  },
  to_column: {
    column_id: "c2",
    table_id: "t-acct",
    db_schema: "core",
    table: "accounts",
    column: "acct_no",
  },
  origin: "inferred",
  confidence: 0.9,
  evidence: {},
  status: "accepted",
  version: 2,
  detected_at: "2026-01-05T10:00:00Z",
  decided_by: ADA.id,
  decided_at: "2026-01-06T10:00:00Z",
};

function api(request: ApiRequest): Response | undefined {
  const route = `${request.method} ${request.path}`;
  switch (route) {
    case "GET /api/v1/me":
      return json(ADA);
    case "GET /api/v1/workspaces":
      return json({ items: [WORKSPACE], next_cursor: null });
    case `GET ${API}`:
      return json(WORKSPACE);
    case `GET ${API}/systems`:
      return json({ items: [CBS], next_cursor: null });
    case `GET ${API}/data-warehouse`:
      return apiError(404, "not_found", "No Data Warehouse");
    case `GET ${SYSTEM}/schema`:
      return json(SCHEMA);
    case `GET ${SYSTEM}/relationships`:
      return json({ items: [INFERRED] });
    default:
      return undefined;
  }
}

let undoCookie: () => void;
beforeAll(() => {
  // React Flow measures its canvas; jsdom has no layout.
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  globalThis.DOMMatrixReadOnly = class {
    m22 = 1;
  } as unknown as typeof DOMMatrixReadOnly;
});
beforeEach(() => {
  undoCookie = setCsrfCookie();
});
afterEach(() => {
  undoCookie();
});

async function relationships() {
  return within(await screen.findByRole("list", { name: "Relationships" }));
}

describe("Source ER diagram", () => {
  it("draws every table and tells declared from accepted-inferred relationships", async () => {
    const requests = renderApp(api, FOLDER);
    const list = await relationships();
    expect(
      list.getByText(/core\.accounts to core\.customers.*, declared/),
    ).toBeInTheDocument();
    expect(
      list.getByText(/core\.transactions to core\.accounts.*, inferred, accepted/),
    ).toBeInTheDocument();
    for (const name of ["customers", "accounts", "transactions", "events"]) {
      const canvas = screen.getByLabelText("ER diagram canvas");
      expect(within(canvas).getByText(new RegExp(`\\.${name}$`))).toBeInTheDocument();
    }
    const asked = requests.find((r) => r.path === `${SYSTEM}/relationships`);
    expect(asked?.query).toMatchObject({ status: "accepted" });
    expect(CSRF_TOKEN).toBeTruthy();
  });

  it("narrows to one Database Schema", async () => {
    renderApp(api, FOLDER);
    await relationships();
    fireEvent.change(screen.getByLabelText("Database Schema"), {
      target: { value: "audit" },
    });
    const canvas = screen.getByLabelText("ER diagram canvas");
    expect(within(canvas).getByText("audit.events")).toBeInTheDocument();
    expect(within(canvas).queryByText("core.accounts")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Relationships" })?.children).toHaveLength(0);
  });

  it("focuses on a table and its neighbours", async () => {
    renderApp(api, FOLDER);
    await relationships();
    fireEvent.change(screen.getByLabelText("Focus on a table"), {
      target: { value: "t-acct" },
    });
    await waitFor(() =>
      expect(
        within(screen.getByLabelText("ER diagram canvas")).queryByText(
          "audit.events",
        ),
      ).not.toBeInTheDocument(),
    );
    const canvas = within(screen.getByLabelText("ER diagram canvas"));
    for (const name of ["core.accounts", "core.customers", "core.transactions"]) {
      expect(canvas.getByText(name)).toBeInTheDocument();
    }
    const list = await relationships();
    expect(list.getAllByRole("listitem")).toHaveLength(2);
  });

  it("says so when the filters leave no table", async () => {
    renderApp(
      (r) =>
        r.path === `${SYSTEM}/schema`
          ? json({ ...SCHEMA, tables: [], db_schemas: [] })
          : api(r),
      FOLDER,
    );
    expect(await screen.findByText("No tables match these filters.")).toBeInTheDocument();
  });
});
