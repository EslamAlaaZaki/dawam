import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Kpi } from "../api/kpis";
import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  apiError,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const KPIS = `/api/v1/workspaces/${WORKSPACE_ID}/kpis`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: ["kpi.edit", "workspace.view"],
  editor: ["kpi.edit", "workspace.view"],
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
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const NIM: Kpi = {
  id: "44444444-4444-4444-8444-444444444444",
  workspace_id: WORKSPACE_ID,
  source_system_id: CBS.id,
  name: "Net interest margin",
  definition: "Interest earned minus interest paid",
  formula_text: "(income - expense) / assets",
  formula_sql: null,
  unit: "%",
  aggregation: "ratio",
  owner: "CFO office",
  refresh_frequency: "monthly",
  targets: [{ label: "FY2027", value: "3.2%" }],
  origin: "user",
  status: "draft",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const SYSTEM_FOLDER = `${PATH}?folder=systems/${CBS.id}/kpis`;
const DW_FOLDER = `${PATH}?folder=dw/kpis`;

/** `kpis`: what the list returns for the Source System, and for the Data Warehouse. */
function backend(
  role: WorkspaceRole,
  kpis: { system: Kpi[]; dw: Kpi[] },
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
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`:
        return json({ set_up: false });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${KPIS}`:
        return (
          handle(r) ??
          json({ items: r.query.data_warehouse === "true" ? kpis.dw : kpis.system, next_cursor: null })
        );
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

describe("the KPIs folders", () => {
  it("lists a Source System's KPIs", async () => {
    renderApp(backend("viewer", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const row = await screen.findByRole("row", { name: /Net interest margin/ });
    expect(row).toHaveTextContent("Draft");
    expect(row).toHaveTextContent("CFO office");
    expect(screen.queryByRole("form", { name: "Add KPI" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and editors can document KPIs.")).toBeInTheDocument();
  });

  it("lists the Data Warehouse's KPIs apart, before it is set up", async () => {
    const requests = renderApp(
      backend("viewer", { system: [NIM], dw: [{ ...NIM, name: "Customer count", source_system_id: null }] }),
      DW_FOLDER,
    );

    expect(await screen.findByRole("row", { name: /Customer count/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument();
    expect(requests.some((r) => r.path === KPIS && r.query.data_warehouse === "true")).toBe(
      true,
    );
  });

  it("shows an empty state", async () => {
    renderApp(backend("viewer", { system: [], dw: [] }), SYSTEM_FOLDER);

    expect(await screen.findByText(/No KPIs yet/)).toBeInTheDocument();
  });

  it("shows a viewer a KPI read-only", async () => {
    renderApp(backend("viewer", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Net interest margin" }));

    const details = screen.getByRole("region", { name: "KPI Net interest margin" });
    expect(within(details).getByText("Interest earned minus interest paid")).toBeInTheDocument();
    expect(within(details).getByText("FY2027: 3.2%")).toBeInTheDocument();
    expect(within(details).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });
});

describe("documenting a KPI", () => {
  it("lets an editor add one under the Source System", async () => {
    const created = { ...NIM };
    const system: Kpi[] = [];
    const requests = renderApp(
      backend("editor", { system, dw: [] }, (r) => {
        if (r.method === "POST" && r.path === KPIS) {
          system.push(created);
          return json(created, 201);
        }
      }),
      SYSTEM_FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Add KPI" });
    fireEvent.change(within(form).getByLabelText("Name"), {
      target: { value: "Net interest margin" },
    });
    fireEvent.change(within(form).getByLabelText("Unit"), { target: { value: "%" } });
    fireEvent.change(within(form).getByLabelText("Targets"), {
      target: { value: "FY2027: 3.2%\n\nFY2028: 3.4%" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));

    await screen.findByRole("form", { name: "KPI Net interest margin" });
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      name: "Net interest margin",
      source_system_id: CBS.id,
      definition: "",
      formula_text: "",
      formula_sql: null,
      unit: "%",
      aggregation: "",
      owner: "",
      refresh_frequency: "",
      targets: [
        { label: "FY2027", value: "3.2%" },
        { label: "FY2028", value: "3.4%" },
      ],
    });
  });

  it("adds a Data Warehouse KPI with no Source System", async () => {
    const requests = renderApp(
      backend("owner", { system: [], dw: [] }, (r) =>
        r.method === "POST" && r.path === KPIS
          ? json({ ...NIM, source_system_id: null }, 201)
          : undefined,
      ),
      DW_FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Add KPI" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Customer count" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toMatchObject({
      name: "Customer count",
      source_system_id: null,
    });
  });

  it("refuses a target that is not `label: value` without asking the server", async () => {
    const requests = renderApp(backend("editor", { system: [], dw: [] }), SYSTEM_FOLDER);

    const form = await screen.findByRole("form", { name: "Add KPI" });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.change(within(form).getByLabelText("Targets"), { target: { value: "95%" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent('"95%" is not');
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });
});

describe("editing a KPI", () => {
  it("lets an editor change its status", async () => {
    const requests = renderApp(
      backend("editor", { system: [NIM], dw: [] }, (r) =>
        r.method === "PATCH" ? json({ ...NIM, status: "in_review", version: 2 }) : undefined,
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Net interest margin" }));
    const form = screen.getByRole("form", { name: "KPI Net interest margin" });
    fireEvent.change(within(form).getByLabelText("Status"), { target: { value: "in_review" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `${KPIS}/${NIM.id}`,
      body: { version: 1, status: "in_review", name: "Net interest margin" },
    });
  });

  it("offers a reload when someone else changed the KPI", async () => {
    renderApp(
      backend("owner", { system: [NIM], dw: [] }, (r) =>
        r.method === "PATCH"
          ? apiError(409, "version_conflict", "Someone else changed this KPI.")
          : undefined,
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Net interest margin" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("deletes a KPI", async () => {
    const system = [NIM];
    const requests = renderApp(
      backend("editor", { system, dw: [] }, (r) => {
        if (r.method === "DELETE") {
          system.length = 0;
          return noContent();
        }
      }),
      SYSTEM_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Net interest margin" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete KPI" }));

    await waitFor(() =>
      expect(screen.queryByRole("form", { name: "KPI Net interest margin" })).not.toBeInTheDocument(),
    );
    expect(requests.some((r) => r.method === "DELETE" && r.path === `${KPIS}/${NIM.id}`)).toBe(
      true,
    );
    expect(await screen.findByText(/No KPIs yet/)).toBeInTheDocument();
  });
});
