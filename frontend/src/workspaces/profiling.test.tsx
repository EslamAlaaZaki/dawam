import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { ImportStatus, TableProfile } from "../api/profiling";
import type { SnapshotSummary, SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import {
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

function workspace(
  role: Workspace["role"],
  permissions: Workspace["permissions"],
): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions,
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const EDITOR = workspace("editor", ["workspace.view", "source_system.profile"]);
const OWNER = workspace("owner", [
  "workspace.view",
  "source_system.profile",
  "source_table.top_n",
]);
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
  table_count: 1,
  column_count: 2,
  routine_count: 0,
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
      comment: null,
      columns: [
        {
          id: "c1",
          name: "city",
          status: "present",
          ordinal: 1,
          data_type: "text",
          is_nullable: true,
          is_pk: false,
          default: null,
          comment: null,
        },
        {
          id: "c2",
          name: "national_id",
          status: "present",
          ordinal: 2,
          data_type: "text",
          is_nullable: true,
          is_pk: false,
          default: null,
          comment: null,
        },
      ],
      constraints: [],
      indexes: [],
    },
  ],
  routines: [],
  removed_columns: [],
  removed_tables: [],
};

const PROFILE: TableProfile = {
  table_id: "t1",
  db_schema: "core",
  name: "customers",
  top_n_enabled: false,
  row_count: 20,
  profiled_at: "2026-01-06T10:00:00Z",
  columns: [
    {
      column_id: "c1",
      name: "city",
      data_type: "text",
      is_protected: false,
      profile: {
        row_count: 20,
        row_cap: 100000,
        sampled: false,
        null_pct: 12.5,
        distinct_count: 3,
        min: "Alexandria",
        max: "Giza",
        avg_len: 5.5,
        max_len: 10,
        top_values: null,
        patterns: [],
        profiled_at: "2026-01-06T10:00:00Z",
      },
    },
    {
      column_id: "c2",
      name: "national_id",
      data_type: "text",
      is_protected: true,
      profile: {
        row_count: 20,
        row_cap: 100000,
        sampled: false,
        null_pct: 0,
        distinct_count: 20,
        min: null,
        max: null,
        avg_len: 14,
        max_len: 14,
        top_values: null,
        patterns: [],
        profiled_at: "2026-01-06T10:00:00Z",
      },
    },
  ],
};

const NOT_IMPORTED: ImportStatus = {
  has_connection: true,
  latest_origin: "connection",
  imported: false,
  disabled_features: [],
};

function backend(
  ws: Workspace,
  requests: { profiling: unknown[]; topN: unknown[] },
  status: ImportStatus = NOT_IMPORTED,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(ws);
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
      case `GET ${SYSTEM}/import`:
        return json(status);
      case `GET ${SYSTEM}/tables/t1/profile`:
        return json(PROFILE);
      case `POST ${SYSTEM}/profiling`:
        requests.profiling.push(r.body);
        return json({ job_id: "j1", status: "queued" }, 202);
      case `PUT ${SYSTEM}/tables/t1/profiling-settings`:
        requests.topN.push(r.body);
        return json({ ...PROFILE, top_n_enabled: true });
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

async function openTable() {
  fireEvent.click(await screen.findByRole("button", { name: "customers" }));
  return screen.findByRole("region", { name: "Profile" });
}

describe("table profiles", () => {
  it("shows the statistics, and no min/max for a protected column", async () => {
    renderApp(backend(VIEWER, { profiling: [], topN: [] }), FOLDER);

    const panel = await openTable();

    const city = (await within(panel).findByText("city")).closest("tr")!;
    expect(city).toHaveTextContent("12.5");
    expect(city).toHaveTextContent("Alexandria");
    expect(city).toHaveTextContent("Giza");
    const protectedRow = within(panel).getByText("national_id").closest("tr")!;
    expect(protectedRow).toHaveTextContent("(protected)");
    expect(protectedRow).not.toHaveTextContent("Alexandria");
    expect(
      within(panel).queryByRole("button", { name: "Profile this table" }),
    ).toBeNull();
    expect(within(panel).queryByRole("checkbox")).toBeNull();
  });

  it("lets an editor start profiling, but not switch top-N", async () => {
    const requests = { profiling: [], topN: [] };
    renderApp(backend(EDITOR, requests), FOLDER);

    const panel = await openTable();
    fireEvent.click(
      await within(panel).findByRole("button", { name: "Profile this table" }),
    );

    await waitFor(() =>
      expect(requests.profiling).toEqual([
        { table_ids: ["t1"], row_cap: 100000, timeout_seconds: 30 },
      ]),
    );
    expect(within(panel).queryByRole("checkbox")).toBeNull();
  });

  it("lets an owner switch top-N on for the table", async () => {
    const requests = { profiling: [], topN: [] };
    renderApp(backend(OWNER, requests), FOLDER);

    const panel = await openTable();
    fireEvent.click(await within(panel).findByRole("checkbox"));

    await waitFor(() => expect(requests.topN).toEqual([{ enabled: true }]));
  });

  it("explains why a Schema-Imported system cannot be profiled", async () => {
    const imported: ImportStatus = {
      has_connection: false,
      latest_origin: "import",
      imported: true,
      disabled_features: ["Profiling"],
    };
    renderApp(backend(EDITOR, { profiling: [], topN: [] }, imported), FOLDER);

    const panel = await openTable();

    expect(await within(panel).findByRole("note")).toHaveTextContent(
      "Schema Import",
    );
    expect(
      within(panel).queryByRole("button", { name: "Profile this table" }),
    ).toBeNull();
  });
});
