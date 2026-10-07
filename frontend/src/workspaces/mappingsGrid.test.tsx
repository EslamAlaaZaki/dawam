import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwTable } from "../api/dwModel";
import type { TableMapping } from "../api/mappings";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const TABLES = `/api/v1/workspaces/${WORKSPACE_ID}/data-warehouse/tables`;

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
    permissions: role === "viewer" ? ["workspace.view"] : ["dw_schema.edit", "workspace.view"],
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const MART: DwTable = {
  id: "a0000000-0000-4000-8000-000000000009",
  layer: "mart",
  name: "dim_customer_m",
  kind: "dimension",
  fact_type: null,
  grain: null,
  is_aggregate: false,
  scd_type: 1,
  is_conformed: false,
  unknown_member: null,
  description: "",
  columns: [],
  naming_violations: [],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
  version: 1,
};
const MAPPING_PATH = `${TABLES}/${MART.id}/mapping`;

function mappingOf(version: number, sql: string, unparsed = false): TableMapping {
  const input = {
    kind: "value" as const,
    table_id: "a0000000-0000-4000-8000-000000000002",
    table_name: "dim_customer",
    column_id: "c0000000-0000-4000-8000-000000000001",
    column_name: "first_name",
  };
  return {
    table_id: MART.id,
    table_name: MART.name,
    layer: "mart",
    source_layer: "core",
    integration_rule: null,
    match_keys: [],
    notes: "",
    version: 0,
    columns: [
      {
        id: version ? "m0000000-0000-4000-8000-000000000001" : null,
        column_id: "c0000000-0000-4000-8000-0000000000aa",
        column_name: "full_name",
        mapping_type: version ? "direct" : "unmapped",
        rule_text: "",
        sql_expression: sql,
        inputs: version && !unparsed ? [input] : [],
        uses: [],
        validation: {
          unparsed,
          errors: unparsed ? [{ code: "unparsed", message: "The SQL does not parse: bad" }] : [],
        },
        version,
      },
    ],
    is_aggregate: false,
    branches: [],
    coverage: [],
    sql: null,
  };
}

function backend(role: WorkspaceRole, mapping: () => Response) {
  return (r: ApiRequest) => {
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`:
        return json({ set_up: true, version: 1 });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET ${TABLES}`:
        return json({ items: [{ ...MART, column_count: 0 }] });
      case `GET ${TABLES}/${MART.id}`:
        return json(MART);
      case `GET ${MAPPING_PATH}`:
        return mapping();
      case `PUT ${MAPPING_PATH}/columns/c0000000-0000-4000-8000-0000000000aa`:
        return mapping();
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

async function openMappings() {
  fireEvent.click(await screen.findByRole("button", { name: "dim_customer_m" }));
  fireEvent.click(await screen.findByRole("button", { name: "Mappings" }));
  return screen.findByRole("region", { name: "Mappings of dim_customer_m" });
}

describe("the mappings editor", () => {
  it("shows a viewer the mapping and its derived inputs read-only", async () => {
    renderApp(
      backend("viewer", () => json(mappingOf(1, "dim_customer.first_name"))),
      `${PATH}?folder=dw/mart/model`,
    );

    const grid = await openMappings();

    const row = within(grid).getByRole("row", { name: /full_name/ });
    expect(row).toHaveTextContent("Direct");
    expect(row).toHaveTextContent("dim_customer.first_name");
    expect(within(grid).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("saves an editor's mapping with the row's version", async () => {
    let saved = false;
    const requests = renderApp(
      backend("editor", () =>
        json(saved ? mappingOf(1, "dim_customer.first_name") : mappingOf(0, "")),
      ),
      `${PATH}?folder=dw/mart/model`,
    );
    const grid = await openMappings();

    fireEvent.change(within(grid).getByLabelText("Type of full_name"), {
      target: { value: "direct" },
    });
    fireEvent.change(within(grid).getByLabelText("SQL for full_name"), {
      target: { value: "dim_customer.first_name" },
    });
    saved = true;
    fireEvent.click(within(grid).getByRole("button", { name: "Save full_name" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      mapping_type: "direct",
      rule_text: "",
      sql_expression: "dim_customer.first_name",
      version: 0,
    });
    expect(await within(grid).findByText("dim_customer.first_name")).toBeInTheDocument();
  });

  it("flags SQL that does not parse", async () => {
    renderApp(
      backend("viewer", () => json(mappingOf(1, "a ||", true))),
      `${PATH}?folder=dw/mart/model`,
    );

    const grid = await openMappings();

    expect(within(grid).getByText("The SQL does not parse: bad")).toBeInTheDocument();
  });
});
