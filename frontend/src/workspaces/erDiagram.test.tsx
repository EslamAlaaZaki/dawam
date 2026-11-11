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
  require_distinct_approver: false,
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
const SCHEMA_FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CBS.id}/source-schema`;

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
  return within(await screen.findByRole("table", { name: "Relationships" }));
}

/** Open a tab below the diagram (Radix tabs activate on mouse down). */
function openTab(name: RegExp) {
  const tab = screen.getByRole("tab", { name });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
}

describe("Source ER diagram", () => {
  it("marks tables that have Protected Columns, naming them", async () => {
    const column = (name: string, is_protected: boolean) =>
      ({ id: `c-${name}`, name, ordinal: 1, is_protected }) as unknown as SourceSchema["tables"][number]["columns"][number];
    renderApp(
      (r) =>
        r.path === `${SYSTEM}/schema`
          ? json({
              ...SCHEMA,
              tables: SCHEMA.tables.map((t) =>
                t.id === "t-cust"
                  ? { ...t, columns: [column("email", true), column("ssn", true), column("id", false)] }
                  : t,
              ),
            })
          : api(r),
      FOLDER,
    );
    const canvas = within(await screen.findByLabelText("ER diagram canvas"));
    expect(canvas.getByRole("img", { name: "2 Protected Columns: email, ssn" })).toBeInTheDocument();
    expect(canvas.getAllByRole("img")).toHaveLength(1);
  });

  it("draws every table and tells declared from accepted-inferred relationships", async () => {
    const requests = renderApp(api, FOLDER);
    const list = await relationships();
    expect(
      list.getByRole("row", { name: /core\.accounts core\.customers.* Declared/ }),
    ).toBeInTheDocument();
    expect(
      list.getByRole("row", { name: /core\.transactions core\.accounts.* Inferred, accepted/ }),
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
    expect(screen.getByText("No relationships between these tables.")).toBeInTheDocument();
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
    expect(list.getAllByRole("row")).toHaveLength(3);
  });

  it("links to the review in the Source schema, with how many wait", async () => {
    renderApp(api, FOLDER);
    const link = await screen.findByRole("link", {
      name: /1 suggested relationship to review.*Review in Source schema/,
    });
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining(
        `folder=systems%2F${CBS.id}%2Fsource-schema&tab=relationships`,
      ),
    );
    fireEvent.click(link);
    expect(
      await screen.findByRole("heading", { name: "Source schema" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find relationships" })).not.toBeInTheDocument();
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

const EDITOR: Workspace = {
  ...WORKSPACE,
  role: "editor",
  permissions: ["workspace.view", "source_system.profile", "source_schema.enhance"],
};

const SUGGESTED: Relationship = {
  ...INFERRED,
  id: "77777777-7777-4777-8777-777777777777",
  from_column: { ...INFERRED.from_column, column: "customer_ref" },
  to_column: {
    column_id: "c3",
    table_id: "t-cust",
    db_schema: "core",
    table: "customers",
    column: "ref",
  },
  origin: "routine",
  confidence: 0.82,
  status: "suggested",
  version: 1,
  decided_by: null,
  decided_at: null,
  evidence: {
    signals: {
      name: { score: 0.8, detail: "customer_ref ~ customers.ref" },
      type: { score: 1, detail: "int / int" },
      unique: { score: 1, detail: "primary key" },
      join: { score: 1, objects: ["core.v_balances", "core.sp_close"] },
      overlap: { ratio: 0.97, values_checked: 40, target_rows_sampled: 100 },
    },
    threshold: 0.6,
  },
};

function review(workspace: Workspace, inferenceJobStatus = "succeeded") {
  const state = { rows: [SUGGESTED, INFERRED] as Relationship[], started: 0, hidden: false };
  const handler = (r: ApiRequest): Response | undefined => {
    const route = `${r.method} ${r.path}`;
    if (route === `GET ${API}`) return json(workspace);
    if (route === `GET ${API}/jobs`) {
      return json({
        items:
          state.started > 0
            ? [{ id: "j1", status: inferenceJobStatus, type: "infer_relationships" }]
            : [],
        next_cursor: null,
      });
    }
    if (route === `GET ${SYSTEM}/relationships`) {
      return json({
        items: state.hidden
          ? []
          : state.rows.filter((x) => x.status === r.query.status),
      });
    }
    if (route === `GET ${SYSTEM}/snapshots`) {
      const { id, source_system_id, origin, job_id, taken_at, is_latest } = SCHEMA;
      return json({
        items: [
          {
            id,
            source_system_id,
            origin,
            job_id,
            taken_at,
            is_latest,
            schema_count: 2,
            table_count: 4,
            column_count: 0,
            routine_count: 0,
          },
        ],
      });
    }
    if (route === `POST ${SYSTEM}/relationship-inference`) {
      state.started += 1;
      state.hidden = false;
      return json({ job_id: "j1", status: "queued" }, 202);
    }
    const decision = r.path.match(/relationships\/([^/]+)\/(accept|reject)$/);
    if (r.method === "POST" && decision) {
      const status = decision[2] === "accept" ? "accepted" : "rejected";
      state.rows = state.rows.map((x) =>
        x.id === decision[1] ? { ...x, status } : x,
      ) as Relationship[];
      return json(state.rows.find((x) => x.id === decision[1]));
    }
    return api(r);
  };
  return { state, handler };
}

describe("Relationship review", () => {
  it("lists suggestions with their evidence, never data values", async () => {
    renderApp(review(EDITOR).handler, SCHEMA_FOLDER);
    await screen.findByRole("tab", { name: /^Relationships/ });
    openTab(/^Relationships/);
    const panel = within(await screen.findByRole("region", { name: "Relationships" }));
    const row = within((await panel.findAllByRole("row"))[1]!);
    expect(row.getByText("core.transactions.customer_ref")).toBeInTheDocument();
    expect(row.getByText("core.customers.ref")).toBeInTheDocument();
    expect(row.getByText("82%")).toBeInTheDocument();
    expect(row.getByText("routine")).toBeInTheDocument();
    for (const chip of ["name", "type", "unique", "join: core.v_balances, core.sp_close", "overlap 97%"]) {
      expect(row.getByText(chip)).toBeInTheDocument();
    }
  });

  it("Find relationships starts the job and the list refills when it finishes", async () => {
    const { handler, state } = review(EDITOR);
    state.hidden = true;
    const requests = renderApp(handler, SCHEMA_FOLDER);
    fireEvent.click(await screen.findByRole("button", { name: "Find relationships" }));
    await waitFor(() => expect(state.started).toBe(1));
    const panel = within(await screen.findByRole("region", { name: "Relationships" }));
    expect(await panel.findByText("core.transactions.customer_ref")).toBeInTheDocument();
    expect(
      requests.find((r) => r.path === `${SYSTEM}/relationship-inference`)?.csrf,
    ).toBe(CSRF_TOKEN);
  });

  it("Accept moves a row to Accepted", async () => {
    renderApp(review(EDITOR).handler, SCHEMA_FOLDER);
    await screen.findByRole("tab", { name: /^Relationships/ });
    openTab(/^Relationships/);
    fireEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Accepted" }), { button: 0 });
    fireEvent.click(screen.getByRole("tab", { name: "Accepted" }));
    expect(await screen.findByText("core.transactions.customer_ref")).toBeInTheDocument();
  });

  it("Reject moves a row to Rejected", async () => {
    renderApp(review(EDITOR).handler, SCHEMA_FOLDER);
    await screen.findByRole("tab", { name: /^Relationships/ });
    openTab(/^Relationships/);
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument(),
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Rejected" }), { button: 0 });
    fireEvent.click(screen.getByRole("tab", { name: "Rejected" }));
    expect(await screen.findByText("core.transactions.customer_ref")).toBeInTheDocument();
  });

  it("shows the lists without buttons to a member without the right", async () => {
    renderApp(review(WORKSPACE).handler, SCHEMA_FOLDER);
    await screen.findByRole("tab", { name: /^Relationships/ });
    openTab(/^Relationships/);
    const panel = within(await screen.findByRole("region", { name: "Relationships" }));
    expect(await panel.findByText("core.transactions.customer_ref")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find relationships" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("is read-only in an archived Workspace", async () => {
    renderApp(review({ ...EDITOR, status: "archived" }).handler, SCHEMA_FOLDER);
    await screen.findByRole("tab", { name: /^Relationships/ });
    openTab(/^Relationships/);
    const panel = within(await screen.findByRole("region", { name: "Relationships" }));
    expect(await panel.findByText("core.transactions.customer_ref")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Find relationships" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });
});
