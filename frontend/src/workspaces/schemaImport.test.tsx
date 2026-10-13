import { fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const IMPORT = `${API}/systems/${SYSTEM_ID}/import`;
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${SYSTEM_ID}/connection`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

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

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions:
      role === "editor"
        ? ["source_system.extract", "workspace.view"]
        : ["workspace.view"],
    version: 1,
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

function backend(role: WorkspaceRole) {
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
        return json({ items: [CBS], next_cursor: null });
    }
    return apiError(404, "not_found", "Not found.");
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("Schema Import downloads", () => {
  it("offers an editor the template and the helper queries of each engine", async () => {
    renderApp(backend("editor"), FOLDER);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Import from template" }));
    const queries = await screen.findByRole("link", { name: /Download helper queries/ });
    expect(screen.getByText(/Use this when DAWAM cannot reach the database/)).toBeVisible();
    expect(queries).toHaveAttribute("href", `${IMPORT}/helper-queries?engine=postgresql`);
    expect(screen.getByRole("link", { name: /Download template/ })).toHaveAttribute(
      "href",
      `${IMPORT}/template`,
    );

    // Engines DAWAM cannot connect to are there too.
    fireEvent.change(screen.getByLabelText("Database engine"), { target: { value: "teradata" } });
    expect(queries).toHaveAttribute("href", `${IMPORT}/helper-queries?engine=teradata`);
    expect(screen.getByRole("option", { name: "IBM Db2 (LUW)" })).toBeInTheDocument();
  });

  it("remembers the tab used last", async () => {
    renderApp(backend("editor"), FOLDER);
    expect(await screen.findByRole("tab", { name: "Live connection" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Import from template" }));
    expect(localStorage.getItem("dawam.connection-tab")).toBe("import");
  });

  it("opens on the remembered tab", async () => {
    localStorage.setItem("dawam.connection-tab", "import");
    renderApp(backend("editor"), FOLDER);

    expect(await screen.findByRole("tab", { name: "Import from template" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("link", { name: /Download template/ })).toBeVisible();
  });

  it("still works when browser storage is unavailable", async () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(blocked);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(blocked);
    renderApp(backend("editor"), FOLDER);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Import from template" }));
    expect(await screen.findByRole("link", { name: /Download template/ })).toBeVisible();
    vi.restoreAllMocks();
  });

  it("is not shown to a viewer", async () => {
    renderApp(backend("viewer"), FOLDER);

    expect(await screen.findByText(/Only owners can see/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Download helper queries/ })).not.toBeInTheDocument();
  });
});
