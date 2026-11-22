import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DwTableSummary } from "../api/dwModel";
import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const PATH = `/workspaces/${WORKSPACE_ID}?folder=dw/staging`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const WORKSPACE = {
  id: WORKSPACE_ID,
  name: "Retail DW",
  description: "",
  domain: "",
  role: "owner",
  permissions: ["dw_schema.edit", "workspace.view"],
  status: "active",
  archived_at: null,
  require_distinct_approver: false,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

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

const TABLES = [
  stagingTable(1, "stg_cbs_banking_customers", { naming_violation_count: 1 }),
  stagingTable(2, "stg_cbs_banking_accounts", { column_count: 6 }),
  stagingTable(3, "stg_crm_crm_contacts"),
  stagingTable(4, "stg_legacy_dump"),
];

function check(severity: string, layer: string) {
  return {
    check_code: `c_${severity}_${layer}`,
    title: "A check",
    category: "naming",
    severity,
    layer,
    object_type: "table",
    object_id: TABLES[0]?.id,
    table_id: TABLES[0]?.id,
    object_name: "stg_cbs_banking_customers",
    message: "",
    fix_hint: "",
    link: "",
  };
}

const SCORE = {
  score: 80,
  grade: "B",
  capped: false,
  calculated_at: "2026-01-05T09:00:00Z",
  layers: [
    { layer: "staging", score: 72, grade: "C", scored: true, table_count: 4 },
    { layer: "core", score: null, grade: null, scored: false, table_count: 0 },
  ],
  tables: [],
  failed_checks: [
    check("warning", "staging"),
    check("warning", "staging"),
    check("error", "core"),
  ],
  checks_passed: 0,
};

function backend(tables: DwTableSummary[], score: object | null = SCORE) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(WORKSPACE);
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [{ layer: "staging", status: "not_started" }],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/data-warehouse`:
        return json({
          set_up: true,
          target_platform: "postgresql",
          version: 1,
        });
      case `GET ${API}/systems`:
        return json({ items: SYSTEMS, next_cursor: null });
      case `GET ${API}/data-warehouse/tables`:
        return json({ items: r.query.layer === "staging" ? tables : [] });
      case `GET ${API}/data-warehouse/score`:
        return score
          ? json(score)
          : json({ error: { code: "not_found", message: "none" } }, 404);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Staging overview", () => {
  it("sums up the Layer in the header and the three cards", async () => {
    renderApp(backend(TABLES), PATH);

    expect(
      await screen.findByRole("heading", { name: /Staging Layer/ }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("heading", { name: /Staging Layer/ })).getByText(
        "Not started",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(/4 tables · 18 columns · 2 Source Systems · /),
    ).toBeInTheDocument();

    const model = screen.getByRole("link", { name: /^Model: 4 Staging Tables/ });
    expect(within(model).getByText("1 with naming issues")).toBeInTheDocument();
    const mappings = screen.getByRole("link", { name: /^Mappings: 18 columns/ });
    expect(
      within(mappings).getByText("generated 1:1 from the source"),
    ).toBeInTheDocument();
    const evaluation = await screen.findByRole("link", {
      name: /^Evaluation: score 72/,
    });
    expect(within(evaluation).getByText("Grade C")).toBeInTheDocument();
    // Only the staging checks count, not the core one.
    expect(within(evaluation).getByText("2 warnings")).toBeInTheDocument();
    expect(within(evaluation).queryByText(/error/)).not.toBeInTheDocument();
    expect(
      within(evaluation).getByText("Not part of the DW score"),
    ).toBeInTheDocument();
  });

  it("hides the naming badge without issues and says Not scored without a score", async () => {
    renderApp(
      backend([stagingTable(1, "stg_cbs_banking_customers")], null),
      PATH,
    );

    const evaluation = await screen.findByRole("link", {
      name: /^Evaluation: not scored/,
    });
    expect(within(evaluation).getByText("Not scored")).toBeInTheDocument();
    expect(screen.queryByText(/with naming issues/)).not.toBeInTheDocument();
  });

  it("lists the tables per Source System, with the rest under Other", async () => {
    renderApp(backend(TABLES), PATH);

    const table = await screen.findByRole("table");
    const cells = (name: RegExp) =>
      within(within(table).getByRole("row", { name }))
        .getAllByRole("cell")
        .map((c) => c.textContent);
    expect(cells(/Core Banking/)).toEqual([
      "Core Bankingcbs",
      "banking",
      "2",
      "10",
      "1",
    ]);
    expect(cells(/CRM/)).toEqual(["CRMcrm", "crm", "1", "4", "0"]);
    expect(cells(/Other/)).toEqual(["Other", "-", "1", "4", "0"]);
  });

  it("opens a sub-folder from its card", async () => {
    renderApp(backend(TABLES), PATH);

    fireEvent.click(
      await screen.findByRole("link", { name: /^Model: 4 Staging Tables/ }),
    );
    expect(await screen.findByText("Staging model")).toBeInTheDocument();
  });

  it("shows an empty state that opens the Model page", async () => {
    renderApp(backend([]), PATH);

    expect(await screen.findByText("No Staging Tables yet")).toBeInTheDocument();
    expect(
      within(screen.getByRole("heading", { name: /Staging Layer/ })).getByText(
        "Not started",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Source Systems · /)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Open Model/ }));
    expect(await screen.findByText("Staging model")).toBeInTheDocument();
  });
});
