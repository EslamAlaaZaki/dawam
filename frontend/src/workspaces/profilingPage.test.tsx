import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type {
  ImportStatus,
  OverviewTable,
  ProfilingGrowth,
  ProfilingOverview,
} from "../api/profiling";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { currentLocation } from "../test/navigation";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(
  role: Workspace["role"],
  permissions: Workspace["permissions"],
): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Bank DW",
    description: "",
    domain: "",
    role,
    permissions,
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const EDITOR = workspace("editor", ["workspace.view", "source_system.profile"]);
const VIEWER = workspace("viewer", ["workspace.view"]);

const SALES: SourceSystem = {
  id: "33333333-3333-4333-8333-333333333333",
  workspace_id: WORKSPACE_ID,
  name: "Sales DB",
  code: "sales",
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};
const SYSTEM = `${API}/systems/${SALES.id}`;
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${SALES.id}/profiling`;

function table(
  id: string,
  name: string,
  extra: Partial<OverviewTable> = {},
): OverviewTable {
  return {
    table_id: id,
    db_schema: "sales",
    name,
    kind: "table",
    row_estimate: 1000,
    column_count: 4,
    profiled_at: "2026-11-09T10:00:00Z",
    rows_sampled: 1000,
    capped: false,
    out_of_date: false,
    findings: [],
    ...extra,
  };
}

const OVERVIEW: ProfilingOverview = {
  latest_snapshot_at: "2026-11-09T09:00:00Z",
  tables: [
    table("t1", "orders", {
      rows_sampled: 100_000,
      row_estimate: 8_412_300,
      capped: true,
      findings: [
        {
          column_id: "c1",
          column: "cancel_reason",
          data_type: "varchar(200)",
          kind: "mostly_null",
          null_pct: 94.2,
          row_count: 100_000,
          capped: true,
          value: null,
        },
        {
          column_id: "c2",
          column: "invoice_no",
          data_type: "varchar(20)",
          kind: "unique_not_key",
          null_pct: 0,
          row_count: 100_000,
          capped: true,
          value: null,
        },
      ],
    }),
    table("t2", "invoices", { out_of_date: true }),
    table("t3", "v_open_orders", {
      kind: "view",
      profiled_at: null,
      rows_sampled: null,
      row_estimate: null,
    }),
  ],
};

const GROWTH: ProfilingGrowth = {
  snapshots: ["2026-08-14T09:00:00Z", "2026-10-02T09:00:00Z", "2026-11-09T09:00:00Z"],
  tables: [
    {
      table_id: "t1",
      db_schema: "sales",
      name: "orders",
      points: [
        { taken_at: "2026-08-14T09:00:00Z", row_estimate: 6_900_000 },
        { taken_at: "2026-10-02T09:00:00Z", row_estimate: 2_000_000 },
        { taken_at: "2026-11-09T09:00:00Z", row_estimate: 8_412_300 },
      ],
      first: 6_900_000,
      latest: 8_412_300,
      change_pct: 21.9,
      per_month: 4_000_000,
      in_12_months: 56_412_300,
      dropped_at: "2026-10-02T09:00:00Z",
    },
  ],
  left_out: [
    { db_schema: "sales", name: "invoices", reason: "unchanged" },
    { db_schema: "sales", name: "v_open_orders", reason: "view" },
  ],
  total_first: 6_900_000,
  total_latest: 8_412_300,
  total_per_month: 4_000_000,
};

const CONNECTED: ImportStatus = {
  has_connection: true,
  latest_origin: "connection",
  imported: false,
  disabled_features: [],
};
const IMPORTED: ImportStatus = {
  has_connection: false,
  latest_origin: "import",
  imported: true,
  disabled_features: ["profiling"],
};

function backend(
  ws: Workspace,
  {
    status = CONNECTED,
    overview = OVERVIEW,
    growth = GROWTH,
    started = [],
  }: {
    status?: ImportStatus;
    overview?: ProfilingOverview;
    growth?: ProfilingGrowth;
    started?: unknown[];
  } = {},
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(ws);
      case `GET ${API}/systems`:
        return json({ items: [SALES], next_cursor: null });
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${SYSTEM}/import`:
        return json(status);
      case `GET ${SYSTEM}/profiling/overview`:
        return json(overview);
      case `GET ${SYSTEM}/profiling/growth`:
        return json(growth);
      case `POST ${SYSTEM}/profiling`:
        started.push(r.body);
        return json({ job_id: "j1", status: "queued" }, 202);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

function openTab(name: RegExp) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), { button: 0 });
}

const rowOf = (text: string) => screen.getByText(text).closest("tr")!;

describe("the Profiling folder", () => {
  it("summarizes coverage, staleness, findings and growth", async () => {
    renderApp(backend(EDITOR), FOLDER);

    expect(await screen.findByText("67%")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 tables")).toBeInTheDocument();
    expect(screen.getByText(/Profiled before the latest Snapshot \(9 Nov\)/)).toBeInTheDocument();
    expect(screen.getByText(/0 all null · 1 mostly null · 1 possible keys/)).toBeInTheDocument();
    expect(await screen.findByText("+4.00M")).toBeInTheDocument();
    expect(screen.getByText("6.90M → 8.41M rows since 14 Aug")).toBeInTheDocument();
  });

  it("lists each table with its status, sample and findings, and filters them", async () => {
    renderApp(backend(EDITOR), FOLDER);

    await screen.findByText("orders");
    const orders = rowOf("orders");
    expect(within(orders).getByText("Profiled")).toBeInTheDocument();
    expect(within(orders).getByText("Capped")).toBeInTheDocument();
    expect(orders).toHaveTextContent("100,000");
    expect(orders).toHaveTextContent("of ~8.41M");
    expect(within(rowOf("invoices")).getByText("Out of date")).toBeInTheDocument();
    const view = rowOf("v_open_orders");
    expect(within(view).getByText("Not profiled")).toBeInTheDocument();
    expect(within(view).getByText("View")).toBeInTheDocument();
    expect(view).toHaveTextContent("Never");

    const filters = screen.getByRole("group", { name: "Filter tables" });
    fireEvent.click(within(filters).getByRole("button", { name: /Out of date/ }));
    expect(screen.queryByText("orders")).toBeNull();
    expect(screen.getByText("invoices")).toBeInTheDocument();
    fireEvent.click(within(filters).getByRole("button", { name: /^All/ }));
    fireEvent.change(screen.getByLabelText("Filter tables by name"), {
      target: { value: "open" },
    });
    expect(screen.getByText("v_open_orders")).toBeInTheDocument();
    expect(screen.queryByText("invoices")).toBeNull();
  });

  it("opens a table's Profile tab in Source schema", async () => {
    renderApp(backend(EDITOR), FOLDER);

    await screen.findByText("orders");
    const link = within(rowOf("orders")).getByRole("link", { name: "Open profile" });
    fireEvent.click(link);

    const url = new URL(currentLocation(), "http://localhost");
    expect(url.searchParams.get("folder")).toBe(`systems/${SALES.id}/source-schema`);
    expect(url.searchParams.get("table")).toBe("t1");
    expect(url.searchParams.get("table_tab")).toBe("profile");
  });

  it("lets an editor profile all, the out-of-date or the selected tables", async () => {
    const started: unknown[] = [];
    renderApp(backend(EDITOR, { started }), FOLDER);

    await screen.findByText("orders");
    fireEvent.click(screen.getByRole("button", { name: "Profile out-of-date tables" }));
    await waitFor(() => expect(started).toHaveLength(1));
    expect(started[0]).toMatchObject({ table_ids: ["t2"], row_cap: 100_000 });

    expect(screen.queryByRole("button", { name: /Profile selected/ })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select sales.orders" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select sales.v_open_orders" }));
    fireEvent.click(screen.getByRole("button", { name: "Profile selected (2)" }));
    await waitFor(() => expect(started).toHaveLength(2));
    expect(started[1]).toMatchObject({ table_ids: ["t1", "t3"] });

    fireEvent.click(screen.getByRole("button", { name: "Profile all tables" }));
    await waitFor(() => expect(started).toHaveLength(3));
    expect(started[2]).toMatchObject({ table_ids: ["t1", "t2", "t3"] });
  });

  it("gives a viewer no profiling actions", async () => {
    renderApp(backend(VIEWER), FOLDER);

    await screen.findByText("orders");
    expect(screen.queryByRole("button", { name: "Profile all tables" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Profile out-of-date tables" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("says profiling is unavailable without a live Connection, with no actions", async () => {
    renderApp(backend(EDITOR, { status: IMPORTED }), FOLDER);

    expect(await screen.findByText(/Profiling is unavailable/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Profile all tables" })).toBeNull();
  });

  it("lists the findings by kind with a link to the column's table", async () => {
    renderApp(backend(EDITOR), FOLDER);

    await screen.findByText("orders");
    openTab(/Findings/);
    const row = (await screen.findByText("cancel_reason")).closest("tr")!;
    expect(within(row).getByText("Mostly null")).toBeInTheDocument();
    expect(row).toHaveTextContent("94.2% null in 100,000 sampled rows");
    expect(within(row).getByRole("link", { name: "Open column" })).toHaveAttribute(
      "href",
      expect.stringContaining("table=t1"),
    );
    expect(rowOf("invoice_no")).toHaveTextContent("Unique in the 100,000 sampled rows");

    const filters = screen.getByRole("group", { name: "Filter findings" });
    fireEvent.click(within(filters).getByRole("button", { name: /Unique, not a key/ }));
    expect(screen.queryByText("cancel_reason")).toBeNull();
    expect(screen.getByText("invoice_no")).toBeInTheDocument();
  });

  it("shows each table's growth with its drop, and what it leaves out", async () => {
    renderApp(backend(EDITOR), FOLDER);

    await screen.findByText("orders");
    openTab(/Data growth/);
    expect(
      await screen.findByText(/Estimated from the row estimates in 3 Snapshots/),
    ).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Data growth" });
    const row = within(table).getByText("orders").closest("tr")!;
    expect(within(row).getByText("Dropped on 2 Oct")).toBeInTheDocument();
    expect(within(row).getByRole("img", { name: "Rows from 6.90M to 8.41M" })).toBeInTheDocument();
    expect(row).toHaveTextContent("+21.9%");
    expect(screen.getByText(/Not shown: sales.invoices \(no change\)/)).toBeInTheDocument();
  });

  it("says growth needs two Snapshots", async () => {
    renderApp(
      backend(EDITOR, {
        growth: { ...GROWTH, snapshots: ["2026-11-09T09:00:00Z"], tables: [], left_out: [] },
      }),
      FOLDER,
    );

    await screen.findByText("orders");
    expect(await screen.findByText("Needs 2 Snapshots")).toBeInTheDocument();
    openTab(/Data growth/);
    expect(await screen.findByText("Growth needs at least 2 Snapshots.")).toBeInTheDocument();
  });

  it("shows the folder's message before the first Snapshot", async () => {
    renderApp(
      backend(EDITOR, { overview: { latest_snapshot_at: null, tables: [] } }),
      FOLDER,
    );

    expect(
      await screen.findByText("Profiles will appear here once the data has been profiled."),
    ).toBeInTheDocument();
  });
});
