import { fireEvent, screen, waitFor } from "@testing-library/react";
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

    fireEvent.click(await screen.findByRole("button", { name: /^Import from template/ }));
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
    expect(await screen.findByRole("button", { name: /^Live connection/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: /^Import from template/ }));
    expect(localStorage.getItem("dawam.connection-tab")).toBe("import");
  });

  it("opens on the remembered tab", async () => {
    localStorage.setItem("dawam.connection-tab", "import");
    renderApp(backend("editor"), FOLDER);

    expect(await screen.findByRole("button", { name: /^Import from template/ })).toHaveAttribute(
      "aria-pressed",
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

    fireEvent.click(await screen.findByRole("button", { name: /^Import from template/ }));
    expect(await screen.findByRole("link", { name: /Download template/ })).toBeVisible();
    vi.restoreAllMocks();
  });

  it("is not shown to a viewer", async () => {
    renderApp(backend("viewer"), FOLDER);

    expect(await screen.findByText(/Only owners can see/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Download helper queries/ })).not.toBeInTheDocument();
  });
});

const REPORT = {
  errors: [],
  warnings: [],
  schema_count: 2,
  table_count: 31,
  column_count: 412,
  routine_count: 6,
};
const WARNING = { sheet: "Columns", row: 14, message: "Data type 'money2' is not known." };
const STATUS = { has_connection: false, latest_origin: null, imported: false, disabled_features: [] };

function uploadBackend(
  handle: (r: ApiRequest) => Response | undefined,
  status: object = STATUS,
) {
  return (r: ApiRequest) =>
    r.method === "GET" && r.path === IMPORT
      ? json(status)
      : (handle(r) ?? backend("editor")(r));
}

async function openUpload(api: ReturnType<typeof uploadBackend>) {
  localStorage.setItem("dawam.connection-tab", "import");
  const requests = renderApp(api, FOLDER);
  const input = await screen.findByLabelText(/Drop the filled template/);
  fireEvent.change(input, {
    target: { files: [new File(["x"], "schema.xlsx")] },
  });
  return requests;
}

describe("Schema Import upload", () => {
  afterEach(() => localStorage.clear());

  it("checks the file and shows the report", async () => {
    const requests = await openUpload(
      uploadBackend((r) =>
        r.method === "POST" && r.path === `${IMPORT}/validate` ? json(REPORT) : undefined,
      ),
    );

    expect(await screen.findByText("schema.xlsx")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Check file" }));
    expect(await screen.findByText("0 errors · 0 warnings")).toBeVisible();
    expect(screen.getByText("412")).toBeVisible();
    expect(requests.find((r) => r.path.endsWith("/validate"))?.body).toMatch(/name="files"/);
  });

  it("blocks the import while the report has errors", async () => {
    await openUpload(
      uploadBackend((r) =>
        r.method === "POST" && r.path === `${IMPORT}/validate`
          ? json({ ...REPORT, errors: [{ sheet: "Tables", row: 3, message: "Name is empty." }] })
          : undefined,
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Check file" }));
    expect(await screen.findByText("Tables · row 3")).toBeVisible();
    expect(screen.getByText("Name is empty.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("needs the warnings accepted, then imports and refreshes", async () => {
    const requests = await openUpload(
      uploadBackend((r) => {
        if (r.method !== "POST") return undefined;
        if (r.path === `${IMPORT}/validate`) return json({ ...REPORT, warnings: [WARNING] });
        if (r.path === `${IMPORT}/upload`) {
          return json({ report: { ...REPORT, warnings: [WARNING] }, imported: true, unchanged: false, snapshot: null });
        }
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Check file" }));
    expect(await screen.findByText("Columns · row 14")).toBeVisible();
    const importButton = screen.getByRole("button", { name: "Import" });
    expect(importButton).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Import anyway, keeping the warnings"));
    expect(importButton).toBeEnabled();
    fireEvent.click(importButton);

    expect(await screen.findByText(/Imported\. A new Snapshot was saved: 31 tables/)).toBeVisible();
    const upload = requests.find((r) => r.path.endsWith("/upload"))?.body as string;
    expect(upload).toContain('name="accept_warnings"');
    expect(upload).toContain("true");
    expect(upload).toContain("postgresql");
    await waitFor(() =>
      expect(requests.filter((r) => r.method === "GET" && r.path === IMPORT).length).toBeGreaterThan(1),
    );
  });

  it("says when there are no differences from the latest Snapshot", async () => {
    await openUpload(
      uploadBackend((r) =>
        r.method === "POST" && r.path === `${IMPORT}/upload`
          ? json({ report: REPORT, imported: true, unchanged: true, snapshot: null })
          : undefined,
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Import" }));
    expect(
      await screen.findByText("No differences from the latest Snapshot; nothing new was saved."),
    ).toBeVisible();
  });

  it("asks the owners for a live connection when there is none", async () => {
    localStorage.setItem("dawam.connection-tab", "import");
    renderApp(
      uploadBackend((r) =>
        r.method === "POST" && r.path === `${IMPORT}/connection-requests`
          ? json({ owners_notified: 2 }, 202)
          : undefined,
      ),
      FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Ask owners to add a live connection" }));
    expect(await screen.findByText("2 owners notified.")).toBeVisible();
  });

  it("shows the message when a connection already exists", async () => {
    localStorage.setItem("dawam.connection-tab", "import");
    renderApp(
      uploadBackend((r) =>
        r.method === "POST" && r.path === `${IMPORT}/connection-requests`
          ? apiError(409, "connection_exists", "This Source System already has a Connection.")
          : undefined,
      ),
      FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Ask owners to add a live connection" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has a Connection");
  });
});
