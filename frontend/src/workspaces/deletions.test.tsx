import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DeletedObjects, SourceSystemDeletion } from "../api/deletions";
import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  CSRF_TOKEN,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: ["objects.delete", "source_system.edit", "workspace.view"],
  editor: ["source_system.edit", "workspace.view"],
  viewer: ["workspace.view"],
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

const DELETED_CRM = { id: CRM.id, name: "CRM", code: "crm", deleted_at: "2026-01-06T09:00:00Z" };

const DELETED: DeletedObjects = { systems: [DELETED_CRM], tables: [] };

const REVIEW: SourceSystemDeletion = {
  system: DELETED_CRM,
  db_schema_count: 1,
  table_count: 2,
  column_count: 9,
  routine_count: 0,
  snapshot_count: 3,
  staging_tables: [
    { id: "a0000000-0000-4000-8000-000000000001", name: "stg_crm_public_customers", has_mapping: true },
  ],
  broken_branches: [
    {
      table_id: "a0000000-0000-4000-8000-000000000002",
      table_name: "core_customer",
      layer: "core",
      branch_id: "b0000000-0000-4000-8000-000000000001",
      branch_name: "CRM",
    },
  ],
  files: [{ id: "f0000000-0000-4000-8000-000000000001", path: "notes.txt", is_document: true }],
  source_link_count: 0,
  rehomed_kpis: [{ id: "c0000000-0000-4000-8000-000000000001", name: "Revenue" }],
  deleted_kpis: [{ id: "c0000000-0000-4000-8000-000000000002", name: "Churn" }],
};

function backend(role: WorkspaceRole, systems: SourceSystem[] = []) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(role));
      case `GET ${API}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/systems`:
        return json({ items: systems, next_cursor: null });
      case `GET ${API}/deleted`:
        return json(DELETED);
      case `GET ${API}/deleted/systems/${CRM.id}`:
        return json(REVIEW);
      case `DELETE ${API}/deleted/systems/${CRM.id}`:
      case `DELETE ${API}/systems/${CRM.id}`:
        return noContent();
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the deleted list", () => {
  it("shows an owner what a permanent delete takes before confirming it", async () => {
    const requests = renderApp(backend("owner"), PATH);

    fireEvent.click(await screen.findByRole("button", { name: "Review CRM" }));
    const review = await screen.findByRole("region", { name: "Permanently delete CRM" });

    expect(within(review).getByText("stg_crm_public_customers")).toBeInTheDocument();
    expect(within(review).getByText("core_customer: CRM")).toBeInTheDocument();
    expect(within(review).getByText("notes.txt")).toBeInTheDocument();
    expect(within(review).getByText("Revenue")).toBeInTheDocument();
    expect(within(review).getByText("Churn")).toBeInTheDocument();
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);

    fireEvent.click(within(review).getByRole("button", { name: "Delete permanently" }));

    await waitFor(() =>
      expect(requests.find((r) => r.method === "DELETE")).toMatchObject({
        path: `${API}/deleted/systems/${CRM.id}`,
        csrf: CSRF_TOKEN,
      }),
    );
  });

  it("is not offered to an editor", async () => {
    const requests = renderApp(backend("editor"), PATH);

    expect(await screen.findByRole("heading", { name: "Retail DW" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Deleted" })).not.toBeInTheDocument();
    expect(requests.some((r) => r.path === `${API}/deleted`)).toBe(false);
  });
});

describe("deleting a Source System", () => {
  const FOLDER = `${PATH}?folder=systems/${CRM.id}`;

  it("lets an owner soft-delete it", async () => {
    const requests = renderApp(backend("owner", [CRM]), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Delete Source System" }));

    await waitFor(() =>
      expect(requests.find((r) => r.method === "DELETE")).toMatchObject({
        path: `${API}/systems/${CRM.id}`,
      }),
    );
  });

  it("is not offered to an editor", async () => {
    renderApp(backend("editor", [CRM]), FOLDER);

    expect(await screen.findByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Source System" })).not.toBeInTheDocument();
  });
});
