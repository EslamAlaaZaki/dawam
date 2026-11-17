import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { CatalogSuggestion } from "../api/catalogSuggestions";
import type { Me } from "../api/queries";
import type { SnapshotSummary, SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { Dialog } from "../components/ui/dialog";
import { json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";
import { ErTableDetails } from "./ErTableDetails";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const workspace = (role: string, permissions: string[]): Workspace => ({
  id: WORKSPACE_ID,
  name: "Retail DW",
  description: "",
  domain: "",
  role,
  permissions,
  status: "active",
  archived_at: null,
  require_distinct_approver: false,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
} as Workspace);

const OWNER = workspace("owner", [
  "workspace.view",
  "source_schema.enhance",
  "assistant.ask",
]);
// May edit the Source Schema but not ask the assistant.
const NO_ASK = workspace("editor", ["workspace.view", "source_schema.enhance"]);
const VIEWER = workspace("viewer", ["workspace.view"]);

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
  column_count: 2,
  routine_count: 0,
};

const column = (id: string, name: string, extra: object = {}) => ({
  id,
  name,
  status: "present",
  ordinal: 1,
  data_type: "integer",
  is_nullable: false,
  is_pk: false,
  default: null,
  comment: "database comment",
  version: 3,
  ...extra,
});

const table = (id: string, name: string, extra: object = {}) => ({
  id,
  db_schema: "core",
  name,
  kind: "table",
  status: "present",
  view_definition: null,
  row_estimate: 10,
  comment: "database table comment",
  version: 2,
  columns: [],
  constraints: [],
  indexes: [],
  ...extra,
});

function schema(tables: object[]): SourceSchema {
  return {
    ...SUMMARY,
    db_schemas: [{ id: "a1", name: "core", status: "present" }],
    tables,
    routines: [],
    removed_columns: [],
    removed_tables: [],
  } as unknown as SourceSchema;
}

const SCHEMA_PENDING = schema([
  table("t1", "customers", {
    columns: [column("c1", "cust_no")],
  }),
  table("t2", "ledger"),
]);

const suggestion = (
  id: string,
  tableId: string,
  tableName: string,
  field: CatalogSuggestion["field"],
  value: string,
  columnId: string | null = null,
  columnName: string | null = null,
  status: CatalogSuggestion["status"] = "pending",
): CatalogSuggestion => ({
  id,
  table_id: tableId,
  db_schema: "core",
  table: tableName,
  column_id: columnId,
  column: columnName,
  field,
  value,
  edited_value: null,
  current_value: null,
  status,
  job_id: null,
  created_at: "2026-01-05T10:00:00Z",
  decided_by: null,
  decided_at: null,
});

const items = () => [
  suggestion("s1", "t1", "customers", "description", "One row per customer"),
  suggestion("s2", "t1", "customers", "classification", "master"),
  suggestion("s3", "t1", "customers", "description", "The customer number", "c1", "cust_no"),
  suggestion("s4", "t2", "ledger", "description", "Posted entries"),
  suggestion("s5", "t2", "ledger", "description", "Old text", null, null, "accepted"),
  {
    ...suggestion("s6", "t2", "ledger", "classification", "log", null, null, "rejected"),
    decided_by: ADA.id,
  },
];

const job = (status: string) => ({
  job_id: "99999999-9999-4999-8999-999999999999",
  status,
  progress: 40,
  error: null,
  made: status === "succeeded" ? 12 : null,
  skipped: status === "succeeded" ? 3 : null,
  created_at: "2026-01-05T10:00:00Z",
  finished_at: null,
});

type Options = {
  ws: Workspace;
  schema?: SourceSchema;
  snapshots?: SnapshotSummary[];
  job?: ReturnType<typeof job> | null;
  /** No suggestion is waiting, so the saved values show. */
  settled?: boolean;
};

/** A fake API whose suggestions change as they are decided; `writes` lists the POSTs and PATCHes. */
function backend(options: Options, writes: { path: string; body: unknown }[] = []) {
  let rows = options.settled ? [] : items();
  const decide = (ids: string[], status: CatalogSuggestion["status"], edited?: string) => {
    rows = rows.map((s) =>
      ids.includes(s.id) && s.status === "pending"
        ? { ...s, status, edited_value: edited ?? null }
        : s,
    );
    return json({ decided: rows.filter((s) => ids.includes(s.id)), skipped: [] });
  };
  return (r: ApiRequest) => {
    if (r.method !== "GET") {
      writes.push({ path: r.path.replace(SYSTEM, ""), body: r.body });
    }
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(options.ws);
      case `GET ${API}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${API}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET ${API}/members`:
        return json({ items: [{ user_id: ADA.id, display_name: "Ada Lovelace" }] });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${SYSTEM}/snapshots`:
        return json({ items: options.snapshots ?? [SUMMARY] });
      case `GET ${SYSTEM}/schema`:
        return json(options.schema ?? SCHEMA_PENDING);
      case `GET ${SYSTEM}/relationships`:
        return json({ items: [] });
      case `GET ${SYSTEM}/catalog-suggestions`:
        return json({ job: options.job ?? null });
      case `POST ${SYSTEM}/catalog-suggestions`:
        return json(job("queued"), 202);
      case `GET ${SYSTEM}/catalog-suggestions/items`: {
        const status = r.query.status ?? "pending";
        const counts = { pending: 0, accepted: 0, rejected: 0, expired: 0 };
        for (const s of rows) {
          counts[s.status]++;
        }
        return json({
          items: rows.filter(
            (s) => s.status === status && (!r.query.table_id || s.table_id === r.query.table_id),
          ),
          counts,
        });
      }
      case `POST ${SYSTEM}/catalog-suggestions/accept`:
        return decide((r.body as { ids: string[] }).ids, "accepted");
      case `POST ${SYSTEM}/catalog-suggestions/reject`:
        return decide((r.body as { ids: string[] }).ids, "rejected");
      case `POST ${SYSTEM}/catalog-suggestions/accept-all`: {
        const only = (r.body as { table_id: string | null }).table_id;
        return decide(
          rows.filter((s) => !only || s.table_id === only).map((s) => s.id),
          "accepted",
        );
      }
      case `POST ${SYSTEM}/catalog-suggestions/accept-edited`: {
        const body = r.body as { id: string; value: string };
        return decide([body.id], "accepted", body.value);
      }
      case `PATCH ${SYSTEM}/tables/t1`:
      case `PATCH ${SYSTEM}/tables/t1/columns/c1`:
        return json({});
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

/** Radix tabs activate on mouse down. */
function openTab(name: RegExp | string) {
  const tab = screen.getByRole("tab", { name });
  fireEvent.mouseDown(tab, { button: 0 });
  fireEvent.click(tab);
}

const bodyOf = (writes: { path: string; body: unknown }[], path: string) =>
  writes.find((w) => w.path === path)?.body;

describe("Suggest catalog entries", () => {
  it("shows the button to a member who may ask the assistant, after Data dictionary", async () => {
    renderApp(backend({ ws: OWNER }), FOLDER);

    const button = await screen.findByRole("button", { name: "Suggest catalog entries" });
    const dictionary = screen.getByRole("button", { name: /Data dictionary/ });
    expect(button).toBeEnabled();
    expect(
      button.compareDocumentPosition(dictionary) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("hides the button from viewers", async () => {
    renderApp(backend({ ws: VIEWER }), FOLDER);
    await screen.findByRole("tab", { name: "Relationships" });
    expect(screen.queryByRole("button", { name: "Suggest catalog entries" })).not.toBeInTheDocument();
  });

  it("hides the button from a member who may edit but not ask the assistant", async () => {
    renderApp(backend({ ws: NO_ASK }), FOLDER);
    await screen.findByRole("tab", { name: /Data catalog/ });
    expect(screen.queryByRole("button", { name: "Suggest catalog entries" })).not.toBeInTheDocument();
  });

  it("is not there before the first Snapshot", async () => {
    renderApp(backend({ ws: OWNER, snapshots: [] }), FOLDER);
    expect(await screen.findByText(/No Snapshot yet/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Suggest catalog entries" })).not.toBeInTheDocument();
  });

  it("starts the job", async () => {
    const writes: { path: string; body: unknown }[] = [];
    renderApp(backend({ ws: OWNER }, writes), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Suggest catalog entries" }));
    await waitFor(() => expect(writes.some((w) => w.path === "/catalog-suggestions")).toBe(true));
  });

  it("is disabled and shows the progress while a run is going", async () => {
    renderApp(backend({ ws: OWNER, job: job("running") }), FOLDER);

    const button = await screen.findByRole("button", { name: "Suggest catalog entries" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(await screen.findByRole("status", { name: "Catalog suggestions" })).toHaveTextContent(
      "40%",
    );
  });

  it("says how many suggestions a finished run made and skipped, with a link to review", async () => {
    renderApp(backend({ ws: OWNER, job: job("succeeded") }), FOLDER);

    const strip = await screen.findByRole("status", { name: "Catalog suggestions" });
    expect(strip).toHaveTextContent("12 suggestions");
    expect(strip).toHaveTextContent("3 fields skipped");
    fireEvent.click(await within(strip).findByRole("button", { name: "Review 4 pending" }));
    expect(await screen.findByRole("table", { name: "Data catalog suggestions" })).toBeInTheDocument();
  });
});

describe("The Data catalog tab", () => {
  it("is not there for a viewer, and ?tab=data-catalog opens the tables for them", async () => {
    renderApp(backend({ ws: VIEWER }), `${FOLDER}&tab=data-catalog`);
    await screen.findByRole("tab", { name: "Relationships" });
    expect(screen.queryByRole("tab", { name: /Data catalog/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Tables and routines" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("opens from ?tab=data-catalog with the pending count", async () => {
    renderApp(backend({ ws: OWNER }), `${FOLDER}&tab=data-catalog`);

    const tab = await screen.findByRole("tab", { name: /Data catalog/ });
    expect(tab).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(tab).toHaveTextContent("4"));
    const list = await screen.findByRole("table", { name: "Data catalog suggestions" });
    expect(within(list).getByText("customers")).toBeInTheDocument();
    expect(within(list).getByText("core")).toBeInTheDocument();
    expect(within(list).getAllByText("(table)").length).toBeGreaterThan(0);
    expect(within(list).getByText("cust_no")).toBeInTheDocument();
    expect(within(list).getByText("The customer number")).toBeInTheDocument();
  });

  it("filters by state and by table, and says rejected ones will not come back", async () => {
    const requests = renderApp(backend({ ws: OWNER }), `${FOLDER}&tab=data-catalog`);
    await screen.findByRole("table", { name: "Data catalog suggestions" });

    openTab(/^Accepted/);
    expect(await screen.findByText("Old text")).toBeInTheDocument();
    expect(screen.queryByText("The customer number")).not.toBeInTheDocument();

    openTab(/^Rejected/);
    expect(await screen.findByText(/Won't be suggested again/)).toBeInTheDocument();
    expect(await screen.findByText("Rejected by Ada Lovelace")).toBeInTheDocument();

    openTab(/^Pending/);
    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "t2" } });
    await waitFor(() =>
      expect(requests.some((r) => r.query.table_id === "t2" && r.query.status === "pending")).toBe(
        true,
      ),
    );
    const list = await screen.findByRole("table", { name: "Data catalog suggestions" });
    await waitFor(() => expect(within(list).queryByText("customers")).not.toBeInTheDocument());
    expect(within(list).getByText("Posted entries")).toBeInTheDocument();
  });

  it("accepts and rejects the selected rows", async () => {
    const writes: { path: string; body: unknown }[] = [];
    renderApp(backend({ ws: OWNER }, writes), `${FOLDER}&tab=data-catalog`);
    await screen.findByRole("table", { name: "Data catalog suggestions" });
    const accept = screen.getByRole("button", { name: /Accept selected/ });
    expect(accept).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select core.customers table description" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select core.ledger table description" }));
    fireEvent.click(screen.getByRole("button", { name: "Accept selected (2)" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept")).toEqual({ ids: ["s1", "s4"] }),
    );

    fireEvent.click(await screen.findByRole("checkbox", { name: "Select all for core.customers" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject selected (2)" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/reject")).toEqual({ ids: ["s2", "s3"] }),
    );
  });

  it("accepts all pending, narrowed to the table filter", async () => {
    const writes: { path: string; body: unknown }[] = [];
    renderApp(backend({ ws: OWNER }, writes), `${FOLDER}&tab=data-catalog`);
    await screen.findByRole("table", { name: "Data catalog suggestions" });

    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "t2" } });
    const all = await screen.findByRole("button", { name: "Accept all pending" });
    await waitFor(() => expect(all).toBeEnabled());
    await screen.findByText("Posted entries");
    expect(within(screen.getByRole("table", { name: "Data catalog suggestions" })).queryByText("customers")).not.toBeInTheDocument();
    fireEvent.click(all);
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept-all")).toEqual({ table_id: "t2" }),
    );
  });

  it("nests tables under their Database Schema, folds them, and accepts a whole schema", async () => {
    const writes: { path: string; body: unknown }[] = [];
    renderApp(backend({ ws: OWNER }, writes), `${FOLDER}&tab=data-catalog`);
    const list = await screen.findByRole("table", { name: "Data catalog suggestions" });

    fireEvent.click(within(list).getByRole("button", { name: /^customers/ }));
    expect(within(list).queryByText("cust_no")).not.toBeInTheDocument();
    expect(within(list).getByText("Posted entries")).toBeInTheDocument();
    fireEvent.click(within(list).getByRole("button", { name: /^core/ }));
    expect(within(list).queryByText("Posted entries")).not.toBeInTheDocument();
    fireEvent.click(within(list).getByRole("button", { name: /^core/ }));

    fireEvent.click(within(list).getByRole("checkbox", { name: "Select all in core" }));
    expect(screen.getByRole("button", { name: "Accept selected (4)" })).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Table"), { target: { value: "schema:core" } });
    const all = screen.getByRole("button", { name: "Accept all pending" });
    await waitFor(() => expect(all).toBeEnabled());
    fireEvent.click(all);
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept")).toEqual({
        ids: ["s1", "s2", "s3", "s4"],
      }),
    );
  });

  it("accepts a row with an edited value", async () => {
    const writes: { path: string; body: unknown }[] = [];
    renderApp(backend({ ws: OWNER }, writes), `${FOLDER}&tab=data-catalog`);
    await screen.findByRole("table", { name: "Data catalog suggestions" });

    fireEvent.click(screen.getByRole("button", { name: "Edit cust_no description before accepting" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Customer key" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and accept" }));

    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept-edited")).toEqual({
        id: "s3",
        value: "Customer key",
      }),
    );
  });
});

describe("Suggestions in place", () => {
  async function openCustomers(ws: Workspace, writes: { path: string; body: unknown }[] = [], s?: SourceSchema) {
    renderApp(backend({ ws, schema: s }, writes), FOLDER);
    fireEvent.click(await screen.findByRole("button", { name: "customers" }));
    return screen.findByRole("region", { name: "Table core.customers" });
  }

  it("shows pending suggestions on the table and its columns with the dashed AI suggested tag", async () => {
    const page = await openCustomers(OWNER);

    expect(await within(page).findByText("One row per customer")).toBeInTheDocument();
    expect(within(page).getByText("The customer number")).toBeInTheDocument();
    expect(within(page).getAllByText("AI suggested")).toHaveLength(3);
    // The classification is master, so the SCD hint row shows (it has no suggestion yet).
    expect(within(page).getByText("SCD hint")).toBeInTheDocument();
  });

  it("accepts a suggestion in place", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openCustomers(OWNER, writes);

    fireEvent.click(await within(page).findByRole("button", { name: "Accept the suggested core.customers classification" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept")).toEqual({ ids: ["s2"] }),
    );
  });

  it("edits then accepts a suggestion in place", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openCustomers(OWNER, writes);

    await within(page).findByText("The customer number");
    fireEvent.click(
      within(page).getByRole("button", {
        name: "Edit the suggested core.customers.cust_no description before accepting",
      }),
    );
    fireEvent.change(within(page).getByLabelText("Description"), {
      target: { value: "Key of the customer" },
    });
    fireEvent.click(within(page).getByRole("button", { name: "Save and accept" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/accept-edited")).toEqual({
        id: "s3",
        value: "Key of the customer",
      }),
    );
  });

  it("rejects a suggestion in place", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openCustomers(OWNER, writes);

    fireEvent.click(
      await within(page).findByRole("button", {
        name: "Reject the suggested core.customers description",
      }),
    );
    await waitFor(() =>
      expect(bodyOf(writes, "/catalog-suggestions/reject")).toEqual({ ids: ["s1"] }),
    );
  });
});

describe("Editing by hand", () => {
  const saved = schema([
    table("t1", "customers", {
      description: "Saved table text",
      classification: "reference",
      scd_hint: "Keep history",
      ai_fields: ["description"],
      columns: [
        column("c1", "cust_no", { description: "Saved column text", ai_fields: ["description"] }),
      ],
    }),
  ]);

  async function openSaved(ws: Workspace, writes: { path: string; body: unknown }[] = []) {
    renderApp(backend({ ws, schema: saved, settled: true }, writes), FOLDER);
    fireEvent.click(await screen.findByRole("button", { name: "customers" }));
    return screen.findByRole("region", { name: "Table core.customers" });
  }

  it("shows the saved values with the AI tag, and a pencil to edit them", async () => {
    const page = await openSaved(OWNER);

    expect(within(page).getByText("Saved table text")).toBeInTheDocument();
    expect(within(page).getByText("reference")).toBeInTheDocument();
    expect(within(page).getByText("Keep history")).toBeInTheDocument();
    // The AI tag marks the accepted description of the table and of the column, not the others.
    expect(within(page).getAllByText("AI")).toHaveLength(2);
  });

  it("saves a table's description with the version it last saw", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openSaved(OWNER, writes);

    fireEvent.click(within(page).getByRole("button", { name: "Edit core.customers description" }));
    fireEvent.change(within(page).getByLabelText("Description"), { target: { value: "New text" } });
    fireEvent.click(within(page).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(bodyOf(writes, "/tables/t1")).toEqual({ version: 2, description: "New text" }),
    );
  });

  it("changes the classification and a column's description by hand", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openSaved(OWNER, writes);

    fireEvent.click(within(page).getByRole("button", { name: "Edit core.customers classification" }));
    fireEvent.change(within(page).getByLabelText("Classification"), { target: { value: "master" } });
    fireEvent.click(within(page).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/tables/t1")).toEqual({ version: 2, classification: "master" }),
    );

    fireEvent.click(within(page).getByRole("button", { name: "Edit core.customers.cust_no description" }));
    fireEvent.change(within(page).getByLabelText("Description"), { target: { value: "Cust key" } });
    fireEvent.click(within(page).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/tables/t1/columns/c1")).toEqual({
        version: 3,
        description: "Cust key",
      }),
    );
  });

  it("clears a description when it is saved empty", async () => {
    const writes: { path: string; body: unknown }[] = [];
    const page = await openSaved(OWNER, writes);

    fireEvent.click(within(page).getByRole("button", { name: "Edit core.customers description" }));
    fireEvent.change(within(page).getByLabelText("Description"), { target: { value: " " } });
    fireEvent.click(within(page).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(bodyOf(writes, "/tables/t1")).toEqual({ version: 2, description: null }),
    );
  });

  it("shows a viewer the accepted values only: no pending text, no pencil, no Comment column", async () => {
    const page = await openCustomersAsViewer();

    expect(within(page).getByText("Saved table text")).toBeInTheDocument();
    expect(within(page).getAllByText("AI")).toHaveLength(2);
    expect(within(page).queryByText("AI suggested")).not.toBeInTheDocument();
    expect(within(page).queryByText("One row per customer")).not.toBeInTheDocument();
    expect(within(page).queryByRole("button", { name: /Edit/ })).not.toBeInTheDocument();
    expect(within(page).queryByRole("columnheader", { name: "Comment" })).not.toBeInTheDocument();
    expect(within(page).getByRole("columnheader", { name: "Description" })).toBeInTheDocument();
    expect(within(page).queryByText("database comment")).not.toBeInTheDocument();
    expect(within(page).queryByText("database table comment")).not.toBeInTheDocument();
  });

  async function openCustomersAsViewer() {
    const requests = renderApp(backend({ ws: VIEWER, schema: saved, settled: true }), FOLDER);
    fireEvent.click(await screen.findByRole("button", { name: "customers" }));
    const page = await screen.findByRole("region", { name: "Table core.customers" });
    expect(requests.some((r) => r.path.includes("catalog-suggestions"))).toBe(false);
    return page;
  }

  it("has no Comment column for an owner either, and no SCD hint row for other classifications", async () => {
    const log = schema([table("t1", "customers", { classification: "log", columns: [column("c1", "cust_no")] })]);
    renderApp(backend({ ws: OWNER, schema: log, settled: true }), FOLDER);
    fireEvent.click(await screen.findByRole("button", { name: "customers" }));
    const page = await screen.findByRole("region", { name: "Table core.customers" });

    expect(within(page).queryByRole("columnheader", { name: "Comment" })).not.toBeInTheDocument();
    expect(within(page).queryByText("SCD hint")).not.toBeInTheDocument();
    expect(within(page).queryByText("database comment")).not.toBeInTheDocument();
  });
});

describe("The ER diagram's table details", () => {
  const renderDetails = (extra: object) => {
    const t = table("t1", "customers", {
      columns: [column("c1", "cust_no", { is_protected: false })],
      indexes: [],
      ...extra,
    }) as unknown as SourceSchema["tables"][number];
    render(
      <Dialog open>
        <ErTableDetails table={t} tables={new Map([[t.id, t]])} edges={[]} onClose={() => {}} onOpenTable={() => {}} />
      </Dialog>,
    );
  };

  it("shows the description, never the database comment", () => {
    renderDetails({ description: "Business text" });

    expect(screen.getByText("Business text")).toBeInTheDocument();
    expect(screen.queryByText("database table comment")).not.toBeInTheDocument();
    expect(screen.queryByText("database comment")).not.toBeInTheDocument();
  });

  it("shows a dash when there is no description", () => {
    renderDetails({});

    expect(screen.queryByText("No description yet.")).not.toBeInTheDocument();
    expect(screen.queryByText("database table comment")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });
});
