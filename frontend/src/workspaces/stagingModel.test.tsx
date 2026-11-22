import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwTableSummary, StagingResult } from "../api/dwModel";
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
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const PATH = `/workspaces/${WORKSPACE_ID}?folder=dw/staging/model`;
const GENERATE = `${API}/data-warehouse/staging/generate`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions:
      role === "viewer"
        ? ["workspace.view"]
        : ["dw_schema.edit", "workspace.view"],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

function system(code: string, name: string): SourceSystem {
  return {
    id: `33333333-3333-4333-8333-3333333333${code.length}${code.charCodeAt(0)}`,
    workspace_id: WORKSPACE_ID,
    name,
    code,
    description: "",
    business_owner: "",
    technical_owner: "",
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const SYSTEMS = [system("cbs", "Core Banking"), system("crm", "CRM")];

function stagingTable(
  n: number,
  name: string,
  extra: Partial<DwTableSummary> = {},
): DwTableSummary {
  return {
    id: `a0000000-0000-4000-8000-00000000000${n}`,
    layer: "staging",
    name,
    kind: "staging",
    fact_type: null,
    grain: null,
    is_aggregate: false,
    scd_type: null,
    is_conformed: false,
    description: "",
    column_count: 4,
    naming_violation_count: 0,
    version: 1,
    review_flags: [],
    status: "present",
    ...extra,
  } as DwTableSummary;
}

const CUSTOMERS = stagingTable(1, "stg_cbs_banking_customers", {
  naming_violation_count: 1,
});
const ACCOUNTS = stagingTable(2, "stg_cbs_banking_accounts", {
  column_count: 6,
});
const CONTACTS = stagingTable(3, "stg_crm_crm_contacts");
const STRAY = stagingTable(4, "stg_legacy_dump");

const RESULT: StagingResult = {
  tables_created: 3,
  columns_created: 18,
  tables_existing: 2,
  flags: [
    {
      table_id: CUSTOMERS.id,
      table_name: "stg_cbs_banking_customers",
      column_name: "notes",
      code: "lossy_type",
      message: "Mapped to a string, the source type has no exact match.",
    },
    {
      table_id: CONTACTS.id,
      table_name: "stg_crm_crm_contacts",
      column_name: null,
      code: "truncated",
      message: "The name was shortened.",
    },
  ],
};

function backend(
  role: WorkspaceRole,
  tables: DwTableSummary[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(role));
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/data-warehouse`:
        return json({ set_up: true, version: 1 });
      case `GET ${API}/systems`:
        return json({ items: SYSTEMS, next_cursor: null });
      case `GET ${API}/data-warehouse/tables`:
        return json({ items: r.query.layer === "staging" ? tables : [] });
    }
    return handle(r);
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Staging model", () => {
  it("groups the Staging Tables by System Code, then Database Schema", async () => {
    renderApp(backend("viewer", [CUSTOMERS, ACCOUNTS, CONTACTS, STRAY]), PATH);

    expect(
      await screen.findByText(/4 tables · 18 columns · 2 Source Systems/),
    ).toBeInTheDocument();
    const tree = screen.getByRole("list", { name: "Source Systems" });
    expect(
      within(tree).getByRole("button", { name: /cbs Core Banking/ }),
    ).toBeInTheDocument();
    expect(
      within(tree).getByRole("button", { name: /crm CRM/ }),
    ).toBeInTheDocument();
    expect(
      within(tree).getByRole("button", { name: /Other/ }),
    ).toBeInTheDocument();
    const banking = within(tree).getByRole("list", {
      name: "Tables of cbs banking",
    });
    expect(
      within(banking)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual(["stg_cbs_banking_customers", "stg_cbs_banking_accounts"]);
    expect(
      within(banking).getByRole("img", { name: "Naming violations" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open a Staging Table from the tree."),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Search by name"), {
      target: { value: "contacts" },
    });
    expect(
      screen.queryByText("stg_cbs_banking_customers"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("stg_crm_crm_contacts")).toBeInTheDocument();
  });

  it("generates for an editor and shows the result with its flags", async () => {
    const tables: DwTableSummary[] = [ACCOUNTS];
    const requests = renderApp(
      backend("editor", tables, (r) => {
        if (r.method === "POST" && r.path === GENERATE) {
          tables.push(CUSTOMERS, CONTACTS);
          return json(RESULT);
        }
      }),
      PATH,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Generate Staging Layer" }),
    );

    const strip = await screen.findByRole("status", {
      name: "Generation result",
    });
    expect(strip).toHaveTextContent(
      "3 Staging Tables and 18 columns created. 2 source tables already had one and were left as they are.",
    );
    expect(strip).toHaveTextContent("2 to review");
    const flags = within(strip).getAllByRole("listitem");
    expect(flags[0]).toHaveTextContent("stg_cbs_banking_customers.notes");
    expect(flags[0]).toHaveTextContent("lossy_type");
    expect(flags[1]).toHaveTextContent("stg_crm_crm_contacts");
    expect(flags[1]).toHaveTextContent("The name was shortened.");
    expect(
      requests.some((r) => r.method === "POST" && r.path === GENERATE),
    ).toBe(true);
    // The tables were refetched.
    expect(
      await screen.findByRole("button", { name: "stg_crm_crm_contacts" }),
    ).toBeInTheDocument();
  });

  it("hides the button from a viewer", async () => {
    renderApp(backend("viewer", [ACCOUNTS]), PATH);

    await screen.findByText("stg_cbs_banking_accounts");
    expect(
      screen.queryByRole("button", { name: "Generate Staging Layer" }),
    ).not.toBeInTheDocument();
  });

  it("shows an error from the call", async () => {
    renderApp(
      backend("editor", [ACCOUNTS], (r) =>
        r.method === "POST" && r.path === GENERATE
          ? apiError(409, "dw_not_set_up", "Set up the Data Warehouse first.")
          : undefined,
      ),
      PATH,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Generate Staging Layer" }),
    );

    await waitFor(() =>
      expect(
        screen.getByText(/Set up the Data Warehouse first/),
      ).toBeInTheDocument(),
    );
  });

  it("explains the empty state, with the button inside it for an editor", async () => {
    renderApp(backend("editor", []), PATH);

    const empty = await screen.findByText(
      /Staging Tables are created from the Source Schema/,
    );
    expect(
      within(empty.closest("div")!).getByRole("button", {
        name: "Generate Staging Layer",
      }),
    ).toBeInTheDocument();
    // Not duplicated in the header.
    expect(
      screen.getAllByRole("button", { name: "Generate Staging Layer" }),
    ).toHaveLength(1);
  });

  it("has no button in the empty state for a viewer", async () => {
    renderApp(backend("viewer", []), PATH);

    await screen.findByText(
      /Staging Tables are created from the Source Schema/,
    );
    expect(
      screen.queryByRole("button", { name: "Generate Staging Layer" }),
    ).not.toBeInTheDocument();
  });
});
