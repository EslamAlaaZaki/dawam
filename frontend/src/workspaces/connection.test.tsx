import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Connection, ConnectionTestResult } from "../api/connections";
import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const SYSTEM_ID = "33333333-3333-4333-8333-333333333333";
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${SYSTEM_ID}/connection`;
const CONNECTION_PATH = `/api/v1/workspaces/${WORKSPACE_ID}/systems/${SYSTEM_ID}/connection`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(role: WorkspaceRole, archived = false): Workspace {
  const owner: Workspace["permissions"] = archived
    ? ["connection.view", "workspace.view"]
    : ["connection.manage", "connection.view", "workspace.view"];
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: role === "owner" ? owner : ["workspace.view"],
    version: 1,
    status: archived ? "archived" : "active",
    archived_at: archived ? "2026-01-06T09:00:00Z" : null,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const CBS: SourceSystem = {
  id: SYSTEM_ID,
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

const SAVED: Connection = {
  id: "44444444-4444-4444-8444-444444444444",
  source_system_id: SYSTEM_ID,
  engine: "postgresql",
  host: "db.internal",
  port: 5432,
  database: "core",
  username: "reader",
  has_password: true,
  options: {},
  allowed_schemas: ["core", "crm"],
  can_write: null,
  last_tested_at: null,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const WORKS: ConnectionTestResult = {
  ok: true,
  error_code: null,
  error: null,
  server_version: "16.4",
  can_write: false,
  available_schemas: ["core", "crm", "restricted"],
  missing_schemas: [],
};

function backend(
  role: WorkspaceRole,
  handle: (r: ApiRequest) => Response | undefined,
  archived = false,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role, archived));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [CBS], next_cursor: null });
      default:
        return handle(r);
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

const NO_CONNECTION = (r: ApiRequest) =>
  r.method === "GET" && r.path === CONNECTION_PATH
    ? apiError(404, "connection_not_found", "This Source System has no Connection yet.")
    : undefined;

describe("a Source System's Connection", () => {
  it("is for owners only", async () => {
    const requests = renderApp(backend("editor", () => undefined), FOLDER);

    expect(
      await screen.findByText(/Only owners can see or change a Source System/),
    ).toBeInTheDocument();
    expect(requests.some((r) => r.path === CONNECTION_PATH)).toBe(false);
  });

  it("tests the settings before saving and lists the schemas found", async () => {
    const requests = renderApp(
      backend("owner", (r) =>
        r.method === "POST" && r.path === `${CONNECTION_PATH}/test` ? json(WORKS) : NO_CONNECTION(r),
      ),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Connection" });
    fireEvent.change(within(form).getByLabelText("Host"), { target: { value: "db.internal" } });
    fireEvent.change(within(form).getByLabelText("Database"), { target: { value: "core" } });
    fireEvent.change(within(form).getByLabelText("Username"), { target: { value: "reader" } });
    fireEvent.change(within(form).getByLabelText("Password"), { target: { value: "s3cret" } });
    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));

    expect(await within(form).findByText(/Connection works \(PostgreSQL 16.4\)/)).toBeVisible();
    expect(requests.find((r) => r.path.endsWith("/test"))?.body).toMatchObject({
      host: "db.internal",
      port: 5432,
      database: "core",
      username: "reader",
      password: "s3cret",
      allowed_schemas: ["public"],
    });
    fireEvent.click(within(form).getByRole("button", { name: "Allow restricted" }));
    expect(within(form).getByLabelText("Allowed Database Schemas")).toHaveValue(
      "public, restricted",
    );
  });

  it("connects a SQL Server database with its own default port and schema", async () => {
    const requests = renderApp(
      backend("owner", (r) =>
        r.method === "POST" && r.path === `${CONNECTION_PATH}/test`
          ? json({ ...WORKS, server_version: "16.0.4135" })
          : NO_CONNECTION(r),
      ),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Connection" });
    fireEvent.change(within(form).getByLabelText("Engine"), { target: { value: "sqlserver" } });
    expect(within(form).getByLabelText("Port")).toHaveValue(1433);
    expect(within(form).getByLabelText("Allowed Database Schemas")).toHaveValue("dbo");
    fireEvent.change(within(form).getByLabelText("Host"), { target: { value: "db.internal" } });
    fireEvent.change(within(form).getByLabelText("Database"), { target: { value: "core" } });
    fireEvent.change(within(form).getByLabelText("Username"), { target: { value: "reader" } });
    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));

    expect(await within(form).findByText(/Connection works \(SQL Server 16.0.4135\)/)).toBeVisible();
    expect(requests.find((r) => r.path.endsWith("/test"))?.body).toMatchObject({
      engine: "sqlserver",
      port: 1433,
      allowed_schemas: ["dbo"],
    });
  });

  it("explains a failed test and warns when the user can write", async () => {
    let result: ConnectionTestResult = {
      ...WORKS,
      ok: false,
      error_code: "authentication_failed",
      error: "The database rejected the username or password.",
    };
    renderApp(
      backend("owner", (r) =>
        r.method === "POST" && r.path === `${CONNECTION_PATH}/test` ? json(result) : NO_CONNECTION(r),
      ),
      FOLDER,
    );
    const form = await screen.findByRole("form", { name: "Connection" });
    for (const [label, value] of [
      ["Host", "db"],
      ["Database", "core"],
      ["Username", "u"],
    ] as const) {
      fireEvent.change(within(form).getByLabelText(label), { target: { value } });
    }

    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Connection failed: The database rejected the username or password.",
    );

    result = { ...WORKS, can_write: true };
    fireEvent.click(within(form).getByRole("button", { name: "Test connection" }));
    expect(await within(form).findByText(/this database user can write/)).toBeVisible();
  });

  it("saves, keeping a stored password when the field is left blank", async () => {
    const requests = renderApp(
      backend("owner", (r) => {
        if (r.method === "GET" && r.path === CONNECTION_PATH) return json(SAVED);
        if (r.method === "PUT" && r.path === CONNECTION_PATH) {
          return json({ ...SAVED, allowed_schemas: ["core"] });
        }
      }),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Connection" });
    expect(within(form).getByLabelText("Host")).toHaveValue("db.internal");
    expect(within(form).getByLabelText("Password")).toHaveValue("");
    expect(within(form).getByLabelText("Password")).toHaveAttribute(
      "placeholder",
      "Saved. Leave blank to keep it.",
    );
    fireEvent.change(within(form).getByLabelText("Allowed Database Schemas"), {
      target: { value: "core" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save Connection" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    const body = requests.find((r) => r.method === "PUT")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ host: "db.internal", allowed_schemas: ["core"] });
    expect(body).not.toHaveProperty("password");
    expect(await within(form).findByText("Connection saved.")).toBeVisible();
  });

  it("is shown read-only to owners of an archived Workspace", async () => {
    const requests = renderApp(
      backend(
        "owner",
        (r) => (r.method === "GET" && r.path === CONNECTION_PATH ? json(SAVED) : undefined),
        true,
      ),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Connection" });
    expect(within(form).getByLabelText("Host")).toHaveValue("db.internal");
    expect(within(form).getByLabelText("Host")).toBeDisabled();
    expect(within(form).getByText(/archived: its Connection is read-only/)).toBeVisible();
    expect(within(form).queryByRole("button", { name: "Save Connection" })).toBeNull();
    expect(within(form).queryByRole("button", { name: "Test connection" })).toBeNull();
    expect(requests.some((r) => r.method !== "GET")).toBe(false);
  });
});
