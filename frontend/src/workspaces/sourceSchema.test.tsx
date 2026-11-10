import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Job } from "../api/jobs";
import type { Me } from "../api/queries";
import type { ImportStatus } from "../api/profiling";
import type { SnapshotSummary, SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { openMenu } from "../test/menus";
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

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: [
    "source_system.extract",
    "job.cancel_any",
    "job.cancel_own",
    "workspace.view",
  ],
  editor: [
    "source_system.extract",
    "source_system.profile",
    "file.upload",
    "job.cancel_own",
    "workspace.view",
  ],
  viewer: ["job.cancel_own", "workspace.view"],
};

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: PERMISSIONS[role],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

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

const JOB_ID = "44444444-4444-4444-8444-444444444444";

function snapshot(overrides: Partial<SnapshotSummary>): SnapshotSummary {
  return {
    id: "55555555-5555-4555-8555-555555555555",
    source_system_id: CBS.id,
    origin: "connection",
    job_id: JOB_ID,
    taken_at: "2026-01-05T10:00:00Z",
    is_latest: true,
    schema_count: 2,
    table_count: 7,
    column_count: 31,
    routine_count: 2,
    ...overrides,
  };
}

function extractJob(status: Job["status"]): Job {
  return {
    id: JOB_ID,
    workspace_id: WORKSPACE_ID,
    type: "extract",
    title: "Extract metadata: Core Banking",
    status,
    progress: status === "succeeded" ? 100 : 0,
    log: "",
    error: null,
    question: null,
    plan: null,
    created_by: ADA.id,
    created_at: "2026-01-05T10:00:00Z",
    started_at: null,
    finished_at: null,
  };
}

function backend(
  role: WorkspaceRole,
  state: { snapshots: SnapshotSummary[]; jobs: Job[] },
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    const handled = handle(r);
    if (handled) {
      return handled;
    }
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(role));
      case `GET ${API}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/jobs`:
        return json({ items: state.jobs, next_cursor: null });
      case `GET ${API}/data-warehouse`:
        return apiError(404, "not_found", "Not set up.");
      case `GET ${SYSTEM}/snapshots`:
        return json({ items: state.snapshots });
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("a Source System's Source Schema", () => {
  it("lists the Snapshots, newest first, for any member", async () => {
    const older = snapshot({
      id: "66666666-6666-4666-8666-666666666666",
      is_latest: false,
      table_count: 6,
      taken_at: "2026-01-04T10:00:00Z",
    });
    renderApp(
      backend("viewer", { snapshots: [snapshot({}), older], jobs: [] }),
      FOLDER,
    );

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Source schema",
      }),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /2 snapshots/ }));
    const list = await screen.findByRole("list", { name: "Snapshots" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Latest");
    expect(items[0]).toHaveTextContent("7 tables and views");
    expect(items[1]).toHaveTextContent("6 tables and views");
    expect(
      screen.queryByRole("button", { name: "Extract metadata" }),
    ).not.toBeInTheDocument();
  });

  it("compares two Snapshots and lists what was added, removed and changed", async () => {
    const older = snapshot({
      id: "66666666-6666-4666-8666-666666666666",
      is_latest: false,
    });
    const latest = snapshot({});
    const col = (name: string, change: "added" | "removed" | "changed") => ({
      id: `c-${name}`,
      name,
      change,
      fields:
        change === "changed"
          ? [{ field: "data_type", before: "text", after: "varchar(20)" }]
          : [],
    });
    renderApp(
      backend("viewer", { snapshots: [latest, older], jobs: [] }, (r) =>
        r.path === `${SYSTEM}/snapshots/${latest.id}/diff/${older.id}`
          ? json({
              from_snapshot_id: older.id,
              to_snapshot_id: latest.id,
              db_schemas: [],
              routines: [],
              tables: [
                {
                  id: "t-orders",
                  db_schema: "public",
                  name: "orders",
                  kind: "table",
                  change: "changed",
                  fields: [],
                  columns: [col("note", "changed"), col("legacy", "removed")],
                },
                {
                  id: "t-gone",
                  db_schema: "public",
                  name: "gone",
                  kind: "table",
                  change: "removed",
                  fields: [],
                  columns: [],
                },
              ],
            })
          : undefined,
      ),
      FOLDER,
    );

    const diff = await screen.findByRole("list", { name: "Differences" });

    expect(diff).toHaveTextContent("Table public.orders: changed");
    expect(diff).toHaveTextContent("Table public.gone: removed");
    const columns = within(diff).getByRole("list", {
      name: "Columns of orders",
    });
    expect(columns).toHaveTextContent(
      "Column note: changed data_type: text to varchar(20)",
    );
    expect(columns).toHaveTextContent("Column legacy: removed");
  });
  it("says when there is no Snapshot yet", async () => {
    renderApp(backend("viewer", { snapshots: [], jobs: [] }), FOLDER);

    expect(await screen.findByText(/No Snapshot yet/)).toBeInTheDocument();
  });

  it("lets an editor start an extraction and shows the new Snapshot once it finishes", async () => {
    const state = { snapshots: [] as SnapshotSummary[], jobs: [] as Job[] };
    const requests = renderApp(
      backend("editor", state, (r) => {
        if (r.method === "POST" && r.path === `${SYSTEM}/extractions`) {
          // The job finishes at once and leaves a Snapshot behind.
          state.jobs = [extractJob("succeeded")];
          state.snapshots = [snapshot({})];
          return json({ job_id: JOB_ID, status: "queued" }, 202);
        }
        return undefined;
      }),
      FOLDER,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Extract metadata" }),
    );

    expect(await screen.findByText(/Extraction started/)).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: /1 snapshot/ }));
    await waitFor(() =>
      expect(screen.getByRole("list", { name: "Snapshots" })).toHaveTextContent(
        "Latest",
      ),
    );
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: `${SYSTEM}/extractions`,
      csrf: CSRF_TOKEN,
    });
  });

  it("says when the system has no Connection to extract from", async () => {
    renderApp(
      backend("editor", { snapshots: [], jobs: [] }, (r) =>
        r.method === "POST"
          ? apiError(
              409,
              "connection_missing",
              "This Source System has no Connection.",
            )
          : undefined,
      ),
      FOLDER,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Extract metadata" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This Source System has no Connection.",
    );
  });

  it("offers any member the data dictionary download, and editors a save to files", async () => {
    renderApp(
      backend("viewer", { snapshots: [snapshot({})], jobs: [] }),
      FOLDER,
    );

    openMenu(await screen.findByRole("button", { name: "Data dictionary" }));
    const link = await screen.findByRole("menuitem", {
      name: "Download (XLSX)",
    });
    expect(link).toHaveAttribute("href", `${SYSTEM}/data-dictionary`);
    expect(
      screen.queryByRole("menuitem", { name: "Save to files" }),
    ).not.toBeInTheDocument();
  });

  it("saves the data dictionary into the file area", async () => {
    const requests: ApiRequest[] = [];
    renderApp(
      backend("editor", { snapshots: [snapshot({})], jobs: [] }, (r) => {
        requests.push(r);
        return r.method === "POST" &&
          r.path === `${SYSTEM}/files/data-dictionary`
          ? json({ id: "f1", name: "data-dictionary-cbs.xlsx" }, 201)
          : undefined;
      }),
      FOLDER,
    );

    openMenu(await screen.findByRole("button", { name: "Data dictionary" }));
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Save to files" }),
    );

    expect(
      await screen.findByText(/Saved data-dictionary-cbs.xlsx/),
    ).toBeInTheDocument();
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      csrf: CSRF_TOKEN,
    });
  });

  it("profiles every present table and view as one job, and marks them once it finishes", async () => {
    const state = {
      snapshots: [snapshot({})],
      jobs: [] as Job[],
      schema: SCHEMA,
    };
    const requests = renderApp(
      backend("editor", state, (r) => {
        switch (`${r.method} ${r.path}`) {
          case `GET ${SYSTEM}/schema`:
            return json(state.schema);
          case `GET ${SYSTEM}/import`:
            return json(CONNECTED);
          case `POST ${SYSTEM}/profiling`:
            // The job finishes at once and leaves every table profiled.
            state.jobs = [profileJob()];
            state.schema = {
              ...SCHEMA,
              tables: SCHEMA.tables.map((t) => ({
                ...t,
                profiled_at: "2026-01-06T10:00:00Z",
              })),
            };
            return json({ job_id: PROFILE_JOB_ID, status: "queued" }, 202);
        }
        return undefined;
      }),
      FOLDER,
    );

    const tables = await screen.findByRole("list", { name: "Tables of core" });
    expect(
      within(tables).getByRole("img", { name: "Not profiled" }),
    ).toBeVisible();
    fireEvent.click(
      await screen.findByRole("button", { name: "Profile all tables" }),
    );

    expect(
      await screen.findByText(
        "Profiling started for 2 tables and views. Follow it under Background jobs.",
      ),
    ).toBeVisible();
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: `${SYSTEM}/profiling`,
      csrf: CSRF_TOKEN,
      body: { table_ids: ["t1", "t2"] },
    });
    await waitFor(() =>
      expect(
        within(tables).getByRole("img", { name: "Profiled" }),
      ).toBeVisible(),
    );
    const views = screen.getByRole("list", { name: "Views of core" });
    expect(within(views).getByRole("img", { name: "Profiled" })).toBeVisible();
  });

  it("does not offer Profile all tables to a viewer", async () => {
    renderApp(
      backend("viewer", { snapshots: [snapshot({})], jobs: [] }, schemaAnd(CONNECTED)),
      FOLDER,
    );

    await screen.findByRole("list", { name: "Tables of core" });
    expect(
      screen.queryByRole("button", { name: "Profile all tables" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer Profile all tables for a Schema-Imported system", async () => {
    const imported: ImportStatus = {
      ...CONNECTED,
      has_connection: false,
      latest_origin: "import",
      imported: true,
    };
    const requests = renderApp(
      backend("editor", { snapshots: [snapshot({})], jobs: [] }, schemaAnd(imported)),
      FOLDER,
    );

    await screen.findByRole("list", { name: "Tables of core" });
    await waitFor(() =>
      expect(requests.some((r) => r.path === `${SYSTEM}/import`)).toBe(true),
    );
    expect(
      await screen.findByRole("button", { name: "Extract metadata" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Profile all tables" }),
    ).not.toBeInTheDocument();
  });
});

const PROFILE_JOB_ID = "77777777-7777-4777-8777-777777777777";

function profileJob(): Job {
  return {
    ...extractJob("succeeded"),
    id: PROFILE_JOB_ID,
    type: "profile",
    title: "Profile 2 table(s): Core Banking",
  };
}

const CONNECTED: ImportStatus = {
  has_connection: true,
  latest_origin: "connection",
  imported: false,
  disabled_features: [],
};

/** Answers the Source Schema below and the given import status. */
function schemaAnd(status: ImportStatus) {
  return (r: ApiRequest) =>
    r.path === `${SYSTEM}/schema`
      ? json(SCHEMA)
      : r.path === `${SYSTEM}/import`
        ? json(status)
        : undefined;
}

const SCHEMA: SourceSchema = {
  ...snapshot({}),
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
      comment: null,
      columns: [],
      constraints: [],
      indexes: [],
    },
    {
      id: "t2",
      db_schema: "core",
      name: "customer_balances",
      kind: "view",
      status: "present",
      view_definition: null,
      row_estimate: null,
      comment: null,
      columns: [],
      constraints: [],
      indexes: [],
    },
  ],
  routines: [],
  removed_columns: [],
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
