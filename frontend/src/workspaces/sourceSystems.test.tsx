import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { currentLocation } from "../test/navigation";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const SYSTEMS = `/api/v1/workspaces/${WORKSPACE_ID}/systems`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: [
    "connection.manage",
    "connection.view",
    "source_system.change_code",
    "source_system.create",
    "source_system.edit",
    "workspace.view",
  ],
  editor: ["source_system.create", "source_system.edit", "workspace.view"],
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
  description: "Accounts and ledgers",
  business_owner: "Head of Retail",
  technical_owner: "DBA team",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

function backend(
  role: WorkspaceRole,
  systems: SourceSystem[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET ${SYSTEMS}`:
        return json({ items: systems, next_cursor: null });
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

function names(): (string | null | undefined)[] {
  return screen
    .getAllByRole("treeitem")
    .map((node) => node.getAttribute("aria-labelledby"))
    .map((id) => document.getElementById(id!)?.textContent);
}

describe("Source Systems in the folder tree", () => {
  it("shows each Source System under Systems with its sub-folders", async () => {
    renderApp(backend("viewer", [CBS]), PATH);

    await screen.findByRole("treeitem", { name: "Core Banking" });

    const all = names();
    const start = all.indexOf("Core Banking");
    expect(all.slice(start, start + 8)).toEqual([
      "Core Banking",
      "Connection | Schema Import",
      "Source Schema",
      "Profiling",
      "PII",
      "Documents",
      "KPIs",
      "Outputs",
    ]);
    expect(screen.getByRole("treeitem", { name: "Core Banking" })).toHaveAttribute(
      "aria-level",
      "3",
    );
  });

  it("opens a system's sub-folder with an empty state", async () => {
    renderApp(backend("viewer", [CBS]), `${PATH}?folder=systems/${CBS.id}/profiling`);

    expect(await screen.findByRole("heading", { name: "Profiling" })).toBeInTheDocument();
  });
});

describe("the Systems folder", () => {
  it("lists the systems for a viewer, who cannot add one", async () => {
    renderApp(backend("viewer", [CBS]), `${PATH}?folder=systems`);

    const row = await screen.findByRole("row", { name: /Core Banking/ });
    expect(row).toHaveTextContent("cbs");
    expect(row).toHaveTextContent("Head of Retail");
    expect(screen.queryByRole("form", { name: "Add Source System" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and editors can add Source Systems.")).toBeInTheDocument();
  });

  it("lets an editor add a Source System and opens its folder", async () => {
    const systems: SourceSystem[] = [];
    const requests = renderApp(
      backend("editor", systems, (r) => {
        if (r.method === "POST" && r.path === SYSTEMS) {
          systems.push(CBS);
          return json(CBS, 201);
        }
      }),
      `${PATH}?folder=systems`,
    );

    const form = await screen.findByRole("form", { name: "Add Source System" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Core Banking" } });
    fireEvent.change(within(form).getByLabelText("System Code"), { target: { value: "cbs" } });
    fireEvent.change(within(form).getByLabelText("Business owner"), {
      target: { value: "Head of Retail" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add Source System" }));

    await waitFor(() =>
      expect(currentLocation()).toBe(`${PATH}?folder=systems%2F${CBS.id}`),
    );
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      name: "Core Banking",
      code: "cbs",
      description: "",
      business_owner: "Head of Retail",
      technical_owner: "",
    });
    expect(await screen.findByRole("treeitem", { name: "Core Banking" })).toBeInTheDocument();
  });

  it("shows why a System Code is refused", async () => {
    renderApp(
      backend("editor", [], (r) =>
        r.method === "POST" && r.path === SYSTEMS
          ? apiError(409, "system_code_taken", "Another Source System has that System Code.")
          : undefined,
      ),
      `${PATH}?folder=systems`,
    );

    const form = await screen.findByRole("form", { name: "Add Source System" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Core" } });
    fireEvent.change(within(form).getByLabelText("System Code"), { target: { value: "cbs" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add Source System" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Another Source System has that System Code.",
    );
  });
});

describe("a Source System's details", () => {
  const FOLDER = `${PATH}?folder=systems/${CBS.id}`;

  it("is read-only for a viewer", async () => {
    renderApp(backend("viewer", [CBS]), FOLDER);

    expect(await screen.findByRole("heading", { name: "Core Banking" })).toBeInTheDocument();
    expect(screen.getByText("DBA team")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("lets an editor edit it but not the System Code", async () => {
    const requests = renderApp(
      backend("editor", [CBS], (r) =>
        r.method === "PATCH" ? json({ ...CBS, technical_owner: "Platform", version: 2 }) : undefined,
      ),
      FOLDER,
    );

    const code = await screen.findByLabelText("System Code");
    expect(code).toHaveAttribute("readonly");
    fireEvent.change(screen.getByLabelText("Technical owner"), { target: { value: "Platform" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `${SYSTEMS}/${CBS.id}`,
      body: { version: 1, technical_owner: "Platform", code: "cbs" },
    });
  });

  it("lets an owner change the System Code", async () => {
    const requests = renderApp(
      backend("owner", [CBS], (r) =>
        r.method === "PATCH" ? json({ ...CBS, code: "core", version: 2 }) : undefined,
      ),
      FOLDER,
    );

    const code = await screen.findByLabelText("System Code");
    expect(code).not.toHaveAttribute("readonly");
    fireEvent.change(code, { target: { value: "core" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Saved.");
    expect(requests.find((r) => r.method === "PATCH")?.body).toMatchObject({ code: "core" });
  });

  it("offers a reload when someone else changed the system", async () => {
    renderApp(
      backend("owner", [CBS], (r) =>
        r.method === "PATCH"
          ? apiError(409, "version_conflict", "Someone else changed this Source System.")
          : undefined,
      ),
      FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(await screen.findByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
