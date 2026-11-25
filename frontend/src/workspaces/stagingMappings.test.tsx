import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { openMenu } from "../test/menus";
import { currentLocation } from "../test/navigation";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const PATH = `/workspaces/${WORKSPACE_ID}?folder=dw/staging/mappings`;
const SYSTEM_ID = "33333333-3333-4333-8333-333333333333";

const ADA = {
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
  role: "viewer",
  permissions: ["workspace.view"],
  status: "active",
  archived_at: null,
  require_distinct_approver: false,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

let n = 0;
function column(
  name: string,
  mapping_type: "direct" | "hashed" | "system",
  extra: Record<string, unknown> = {},
) {
  n += 1;
  return {
    column_id: `b0000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    column_name: name,
    data_type: { type: "string", length: 40 },
    is_system: mapping_type === "system",
    role: mapping_type === "system" ? "audit" : "attribute",
    status: "present",
    review_flags: [],
    mapping_type,
    source_column_name: mapping_type === "system" ? null : name,
    source_data_type: mapping_type === "system" ? null : "varchar(40)",
    ...extra,
  };
}

const MAPPINGS = {
  tables: [
    {
      table_id: "a0000000-0000-4000-8000-000000000001",
      table_name: "stg_cbs_customers",
      status: "present",
      review_flags: [],
      system_id: SYSTEM_ID,
      system_name: "Core Banking",
      system_code: "cbs",
      source_db_schema: "dbo",
      source_table_name: "customers",
      columns: [
        column("customer_id", "direct"),
        column("email", "hashed"),
        column("full_name", "direct", {
          review_flags: [{ code: "truncated", message: "cut" }],
        }),
        column("_loaded_at", "system"),
      ],
    },
    {
      table_id: "a0000000-0000-4000-8000-000000000002",
      table_name: "stg_cbs_accounts",
      status: "present",
      review_flags: [],
      system_id: SYSTEM_ID,
      system_name: "Core Banking",
      system_code: "cbs",
      source_db_schema: "dbo",
      source_table_name: "accounts",
      columns: [column("account_id", "direct"), column("_loaded_at", "system")],
    },
  ],
  totals: { tables: 2, columns: 6, direct: 3, hashed: 1, audit: 2, to_review: 1 },
};

function backend(mappings: object) {
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
          dw_modeling: [],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/data-warehouse`:
        return json({ set_up: true, target_platform: "postgresql", version: 1 });
      case `GET ${API}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET ${API}/data-warehouse/staging/mappings`:
        return json(mappings);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  n = 0;
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Staging Mappings page", () => {
  it("shows the summary cards from the totals", async () => {
    renderApp(backend(MAPPINGS), PATH);

    expect(
      await screen.findByRole("heading", { name: /Staging mappings/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Fully mapped")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("6 / 6 columns")).toBeInTheDocument();
    const card = (label: string) => screen.getByRole("group", { name: label });
    expect(within(card("Copied direct")).getByText("3")).toBeInTheDocument();
    expect(within(card("Hashed (PII)")).getByText("1")).toBeInTheDocument();
    expect(within(card("To review")).getByText("1")).toBeInTheDocument();
  });

  it("badges each table and column, and links hashed rows to the PII page", async () => {
    renderApp(backend(MAPPINGS), PATH);

    const group = await screen.findByRole("button", {
      name: "stg_cbs_customers, 4 columns",
    });
    expect(within(group).getByText("1 hashed")).toBeInTheDocument();
    expect(within(group).getByText("1 to review")).toBeInTheDocument();
    expect(within(group).getByText("Core Banking")).toBeInTheDocument();
    expect(screen.getByText("Truncated")).toBeInTheDocument();
    expect(screen.getAllByText("Audit", { selector: "span.bg-transparent" })[0]).toBeInTheDocument();
    expect(screen.getAllByText("none").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("link", { name: "Change in PII" }));
    expect(currentLocation()).toBe(
      `/workspaces/${WORKSPACE_ID}?folder=${encodeURIComponent(`systems/${SYSTEM_ID}/pii`)}`,
    );
  });

  it("filters by segment and by text, hiding groups without a match", async () => {
    renderApp(backend(MAPPINGS), PATH);
    await screen.findAllByText("customer_id");

    expect(screen.getByRole("button", { name: /^Hasheds*1$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^To reviews*1$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Audits*2$/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Hashed/ }));
    expect(screen.getAllByText("email").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("customer_id")).toHaveLength(0);
    expect(screen.queryByText("__never__")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^stg_cbs_accounts/ }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    fireEvent.change(screen.getByLabelText("Filter by table or column"), {
      target: { value: "account" },
    });
    expect(screen.getAllByText("account_id").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("customer_id")).toHaveLength(0);
    expect(screen.queryByText("__never__")).not.toBeInTheDocument();
  });

  it("offers XLSX and CSV downloads of the staging mapping sheet", async () => {
    renderApp(backend(MAPPINGS), PATH);

    const button = await screen.findByRole("button", {
      name: /Export mapping sheet/,
    });
    openMenu(button);
    const xlsx = await screen.findByRole("menuitem", { name: /Download XLSX/ });
    expect(xlsx.getAttribute("href")).toBe(
      `${API}/data-warehouse/mapping-sheet?layer=staging&format=xlsx`,
    );
    expect(
      screen.getByRole("menuitem", { name: /Download CSV/ }).getAttribute("href"),
    ).toBe(`${API}/data-warehouse/mapping-sheet?layer=staging&format=csv`);
  });

  it("explains an empty Layer and opens the Model page", async () => {
    renderApp(
      backend({
        tables: [],
        totals: { tables: 0, columns: 0, direct: 0, hashed: 0, audit: 0, to_review: 0 },
      }),
      PATH,
    );

    expect(await screen.findByText("No staging mappings yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Open Model/ }));
    expect(currentLocation()).toBe(
      `/workspaces/${WORKSPACE_ID}?folder=dw%2Fstaging%2Fmodel`,
    );
  });
});
