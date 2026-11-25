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

  it("groups the tree by Source System and marks each table's state", async () => {
    renderApp(backend(MAPPINGS), PATH);

    const tree = await screen.findByRole("list", {
      name: "Staging tables by Source System",
    });
    const group = within(tree).getByRole("button", { name: /^Core Banking/ });
    expect(group).toHaveAttribute("aria-expanded", "true");
    expect(group).toHaveTextContent("2");
    const row = (name: string) =>
      within(tree).getByRole("button", { name }).closest("li")!;
    expect(
      within(row("stg_cbs_customers")).getByRole("img", { name: "To review" }),
    ).toBeInTheDocument();
    expect(
      within(row("stg_cbs_accounts")).getByRole("img", { name: "Copied direct" }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Legend" })).getByText("Has hashed"),
    ).toBeInTheDocument();
    // No filter, so no per-table column count.
    expect(row("stg_cbs_customers")).not.toHaveTextContent("4");

    fireEvent.click(group);
    expect(
      within(tree).queryByRole("button", { name: "stg_cbs_customers" }),
    ).not.toBeInTheDocument();
  });

  it("badges the selected table and its columns, and links hashed rows to the PII page", async () => {
    renderApp(backend(MAPPINGS), PATH);

    const region = await screen.findByRole("region", {
      name: "Mappings of stg_cbs_customers",
    });
    expect(
      within(region).getByRole("heading", { name: "stg_cbs_customers" }),
    ).toBeInTheDocument();
    expect(within(region).getByText("1 hashed")).toBeInTheDocument();
    expect(within(region).getByText("1 to review")).toBeInTheDocument();
    expect(within(region).getByText("Core Banking")).toBeInTheDocument();
    expect(within(region).getByText(/4 columns/)).toBeInTheDocument();
    expect(within(region).getByText("dbo.customers")).toBeInTheDocument();
    expect(within(region).getByText("Truncated")).toBeInTheDocument();
    expect(
      within(region).getAllByText("Audit", { selector: "span.bg-transparent" })[0],
    ).toBeInTheDocument();
    expect(within(region).getAllByText("none").length).toBeGreaterThan(0);

    fireEvent.click(within(region).getByRole("link", { name: "Change in PII" }));
    expect(currentLocation()).toBe(
      `/workspaces/${WORKSPACE_ID}?folder=${encodeURIComponent(`systems/${SYSTEM_ID}/pii`)}`,
    );
  });

  it("shows the clicked table's columns", async () => {
    renderApp(backend(MAPPINGS), PATH);
    await screen.findByRole("region", { name: "Mappings of stg_cbs_customers" });

    fireEvent.click(screen.getByRole("button", { name: "stg_cbs_accounts" }));
    const region = screen.getByRole("region", { name: "Mappings of stg_cbs_accounts" });
    expect(within(region).getAllByText("account_id").length).toBeGreaterThan(0);
    expect(screen.queryByText("customer_id")).not.toBeInTheDocument();
  });

  it("narrows the tree by segment, counts matches and falls back to the first visible table", async () => {
    renderApp(backend(MAPPINGS), PATH);
    await screen.findByRole("region", { name: "Mappings of stg_cbs_customers" });

    expect(screen.getByRole("button", { name: /^Hasheds*1$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^To reviews*1$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Audits*2$/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "stg_cbs_accounts" }));
    fireEvent.click(screen.getByRole("button", { name: /^Hashed/ }));
    const tree = screen.getByRole("list", { name: "Staging tables by Source System" });
    expect(
      within(tree).queryByRole("button", { name: "stg_cbs_accounts" }),
    ).not.toBeInTheDocument();
    // The chosen table is filtered out, so the first visible one shows.
    const region = screen.getByRole("region", { name: "Mappings of stg_cbs_customers" });
    expect(within(region).getAllByText("email").length).toBeGreaterThan(0);
    expect(within(region).queryByText("customer_id")).not.toBeInTheDocument();
    expect(
      within(within(tree).getByRole("button", { name: "stg_cbs_customers" }).closest("li")!)
        .getByText("1"),
    ).toBeInTheDocument();

    // No column left: the right side says so.
    fireEvent.change(screen.getByLabelText("Search by name"), {
      target: { value: "nothing_here" },
    });
    expect(screen.getByText("No table matches.")).toBeInTheDocument();
    expect(screen.getByText("No column matches this filter.")).toBeInTheDocument();
  });

  it("searches by table or column name in the tree, with no separate text filter", async () => {
    renderApp(backend(MAPPINGS), PATH);
    await screen.findByRole("region", { name: "Mappings of stg_cbs_customers" });

    expect(screen.queryByLabelText("Filter by table or column")).not.toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: "Search by name" });
    expect(search).toHaveAttribute("placeholder", "A table or column");

    fireEvent.change(search, { target: { value: "account" } });
    const tree = screen.getByRole("list", { name: "Staging tables by Source System" });
    expect(within(tree).queryByRole("button", { name: "stg_cbs_customers" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Mappings of stg_cbs_accounts" })).toBeInTheDocument();

    // A column name finds its table; only matching columns show.
    fireEvent.change(search, { target: { value: "full_name" } });
    const region = screen.getByRole("region", { name: "Mappings of stg_cbs_customers" });
    expect(within(region).getAllByText("full_name").length).toBeGreaterThan(0);
    expect(within(region).queryByText("customer_id")).not.toBeInTheDocument();
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
