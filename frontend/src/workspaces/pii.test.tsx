import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { PiiFinding } from "../api/pii";
import type { SourceSchema } from "../api/snapshots";
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
  status: Workspace["status"] = "active",
): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Bank DW",
    description: "",
    domain: "",
    role,
    permissions,
    status,
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const OWNER = workspace("owner", ["workspace.view", "pii.review"]);
const VIEWER = workspace("viewer", ["workspace.view"]);

const CRM: SourceSystem = {
  id: "33333333-3333-4333-8333-333333333333",
  workspace_id: WORKSPACE_ID,
  name: "CRM",
  code: "crm",
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};
const SYSTEM = `${API}/systems/${CRM.id}`;
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CRM.id}/pii`;

function table(id: string, name: string, kind: "table" | "view") {
  return {
    id,
    db_schema: "crm",
    name,
    kind,
    status: "present" as const,
    view_definition: null,
    row_estimate: null,
    comment: null,
    columns: [],
    constraints: [],
    indexes: [],
  };
}

const SCHEMA = {
  id: "55555555-5555-4555-8555-555555555555",
  source_system_id: CRM.id,
  origin: "connection",
  job_id: null,
  taken_at: "2026-01-05T10:00:00Z",
  is_latest: true,
  schema_count: 1,
  table_count: 3,
  column_count: 0,
  routine_count: 0,
  db_schemas: [],
  tables: [
    table("t1", "contacts", "table"),
    table("t2", "complaints", "table"),
    table("t3", "v_active", "view"),
  ],
  routines: [],
  removed_columns: [],
  removed_tables: [],
} as unknown as SourceSchema;

function finding(
  id: string,
  column: string,
  status: PiiFinding["status"],
  extra: Partial<PiiFinding> = {},
): PiiFinding {
  return {
    id,
    column_id: `c-${id}`,
    table_id: "t1",
    db_schema: "crm",
    table: "contacts",
    column,
    rule: "national_id",
    category: "direct_identifier",
    confidence: 0.92,
    evidence: "94% of sampled values look like a national ID",
    status,
    is_sensitive: false,
    is_protected: status !== "dismissed",
    detected_at: "2026-01-06T10:00:00Z",
    decided_by: null,
    decided_at: null,
    ...extra,
  };
}

interface Requests {
  scans: unknown[];
  decisions: string[];
}

function backend(ws: Workspace, requests: Requests) {
  const findings = [
    finding("f1", "id_number", "suggested"),
    finding("f2", "email", "confirmed", { rule: "email", category: "quasi_identifier" }),
  ];
  return (r: ApiRequest) => {
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(ws);
      case `GET ${API}/systems`:
        return json({ items: [CRM], next_cursor: null });
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${SYSTEM}/schema`:
        return json(SCHEMA);
      case `GET ${SYSTEM}/pii-findings`: {
        const status = r.query.status;
        return json({ items: findings.filter((f) => f.status === status) });
      }
      case `POST ${SYSTEM}/pii-scans`:
        requests.scans.push(r.body);
        return json({ job_id: "j1", status: "queued" }, 202);
    }
    const decision = key.match(/^POST .*\/pii-findings\/(f\d)\/(confirm|dismiss)$/);
    if (decision) {
      requests.decisions.push(`${decision[1]} ${decision[2]}`);
      const found = findings.find((f) => f.id === decision[1])!;
      found.status = decision[2] === "confirm" ? "confirmed" : "dismissed";
      return json(found);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

function openTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0 });
}

describe("the PII folder", () => {
  it("lists the review queue with the column in full, the evidence and a Protected badge", async () => {
    renderApp(backend(OWNER, { scans: [], decisions: [] }), FOLDER);

    const row = (await screen.findByText("crm.contacts.id_number")).closest("tr")!;
    expect(row).toHaveTextContent("national_id");
    expect(row).toHaveTextContent("92%");
    expect(row).toHaveTextContent("94% of sampled values");
    expect(within(row).getByText("Protected")).toBeInTheDocument();
    expect(screen.queryByText("crm.contacts.email")).toBeNull();
  });

  it("moves a finding to the Confirmed tab when it is confirmed", async () => {
    const requests: Requests = { scans: [], decisions: [] };
    renderApp(backend(OWNER, requests), FOLDER);

    const row = (await screen.findByText("crm.contacts.id_number")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(requests.decisions).toEqual(["f1 confirm"]));
    await waitFor(() =>
      expect(screen.queryByText("crm.contacts.id_number")).toBeNull(),
    );
    openTab("Confirmed");
    expect(await screen.findByText("crm.contacts.id_number")).toBeInTheDocument();
    expect(screen.getByText("crm.contacts.email")).toBeInTheDocument();
  });

  it("moves a finding to the Dismissed tab when it is dismissed", async () => {
    const requests: Requests = { scans: [], decisions: [] };
    renderApp(backend(OWNER, requests), FOLDER);

    const row = (await screen.findByText("crm.contacts.id_number")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(requests.decisions).toEqual(["f1 dismiss"]));
    openTab("Dismissed");
    expect(await screen.findByText("crm.contacts.id_number")).toBeInTheDocument();
  });

  it("shows findings without the buttons to a member without the review right", async () => {
    renderApp(backend(VIEWER, { scans: [], decisions: [] }), FOLDER);

    expect(await screen.findByText("crm.contacts.id_number")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Scan values" })).toBeNull();
  });

  it("scans every table (not views) by default, and starts the job", async () => {
    const requests: Requests = { scans: [], decisions: [] };
    renderApp(backend(OWNER, requests), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Scan values" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findAllByRole("checkbox")).toHaveLength(2);
    expect(within(dialog).queryByText("crm.v_active")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Start scan" }));

    await waitFor(() =>
      expect(requests.scans).toEqual([{ table_ids: ["t1", "t2"], sample_size: 1000 }]),
    );
  });

  it("narrows the scan to the tables ticked", async () => {
    const requests: Requests = { scans: [], decisions: [] };
    renderApp(backend(OWNER, requests), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Scan values" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: "crm.complaints" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Start scan" }));

    await waitFor(() => expect(requests.scans).toEqual([{ table_ids: ["t1"], sample_size: 1000 }]));
  });

  it("offers no scan or decisions in an archived Workspace", async () => {
    const archived = workspace("owner", ["workspace.view"], "archived");
    renderApp(backend(archived, { scans: [], decisions: [] }), FOLDER);

    expect(await screen.findByText("crm.contacts.id_number")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan values" })).toBeNull();
  });
});
