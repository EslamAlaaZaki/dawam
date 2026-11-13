import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { KpiSuggestionJob } from "../api/kpiSuggestions";
import type { Kpi, RejectedRuleSuggestion, RuleSuggestion } from "../api/kpis";
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
  owner: ["kpi.edit", "kpi.view_review", "workspace.view"],
  editor: ["kpi.edit", "kpi.view_review", "workspace.view"],
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
    require_distinct_approver: false,
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
  rationale: null,
  based_on: [],
  status: "draft",
  review_state: "live",
  decision: "added",
  decided_by: ADA.id,
  decided_at: "2026-01-05T09:00:00Z",
  link_count: 0,
  feasibility: null,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const AI_KPI: Kpi = {
  ...NIM,
  id: "55555555-5555-4555-8555-555555555555",
  name: "Cost to income",
  definition: "Operating cost over operating income",
  origin: "ai",
  rationale: "The ledger holds cost and income columns.",
  review_state: "accepted",
  decision: "accepted",
};

const SUGGESTED: Kpi = {
  ...AI_KPI,
  id: "66666666-6666-4666-8666-666666666666",
  name: "Dormant account rate",
  definition: "Accounts with no transaction in a year",
  rationale: "accounts.last_txn_date measures dormancy.",
  owner: "",
  review_state: "suggested",
  decision: null,
  decided_by: null,
  decided_at: null,
};

const REJECTED: Kpi = {
  ...SUGGESTED,
  id: "77777777-7777-4777-8777-777777777777",
  name: "Branch cash utilisation",
  review_state: "rejected",
  decision: "rejected",
  decided_by: ADA.id,
};

const DW_KPI_ID = "88888888-8888-4888-8888-888888888888";

const RULE: RuleSuggestion = {
  key: "sum:amount",
  kind: "sum",
  name: "Sum of amount (fact_loan)",
  definition: "Sum of Amount over all rows of fact_loan.",
  formula_text: "SUM(amount)",
  formula_sql: "SELECT SUM(amount) AS sum_amount FROM core.fact_loan",
  aggregation: "sum",
  layer: "core",
  table_name: "fact_loan",
  column_ids: ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
  column_labels: ["core.fact_loan.amount"],
  feasibility: "needs_mapping",
};

const RULE_REJECTED: RejectedRuleSuggestion = {
  ...RULE,
  key: "avg:fee",
  kind: "avg",
  name: "Average of fee (fact_loan)",
  decided_by: ADA.id,
  decided_at: "2026-01-06T09:00:00Z",
};

const inCatalog =(kpi: Kpi) => kpi.review_state === "live" || kpi.review_state === "accepted";

const SYSTEM_FOLDER = `${PATH}?folder=systems/${CBS.id}/kpis`;
const DW_FOLDER = `${PATH}?folder=dw/kpis`;

/**
 * `kpis`: what the list returns for the Source System, and for the Data Warehouse page (every
 * KPI of the Workspace). `rules`: the rule suggestions that come with the Data Warehouse page.
 */
function backend(
  role: WorkspaceRole,
  kpis: { system: Kpi[]; dw: Kpi[] },
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
  rules: { open: RuleSuggestion[]; rejected: RejectedRuleSuggestion[] } = {
    open: [],
    rejected: [],
  },
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
        return json({ items: [{ user_id: ADA.id, display_name: ADA.display_name }] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/data-warehouse`:
        return json({ set_up: false });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${KPIS}`: {
        const items = r.query.source_system_id ? kpis.system : kpis.dw;
        const withRules = r.query.rule_suggestions === "true";
        return (
          handle(r) ??
          json({
            items: r.query.review === "all" ? items : items.filter(inCatalog),
            next_cursor: null,
            rule_suggestions: withRules ? rules.open : [],
            rejected_rule_suggestions: withRules ? rules.rejected : [],
          })
        );
      }
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

/** Opens the Add KPI popup from the folder's header. */
async function openAddKpi() {
  fireEvent.click(await screen.findByRole("button", { name: "Add KPI" }));
  return screen.findByRole("dialog", { name: "Add KPI" });
}

/** Opens the Edit popup from the one KPI's row. */
async function openEditKpi() {
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  return screen.findByRole("dialog", { name: "Edit KPI" });
}

describe("the KPIs folders", () => {
  it("lists a Source System's KPIs", async () => {
    const requests = renderApp(backend("viewer", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const row = await screen.findByRole("row", { name: /Net interest margin/ });
    expect(row).toHaveTextContent("Draft");
    expect(row).toHaveTextContent("CFO office");
    expect(row).toHaveTextContent("Manual");
    expect(row).toHaveTextContent("Words only");
    await waitFor(() => expect(row).toHaveTextContent("Added by Ada Lovelace"));
    expect(screen.queryByRole("button", { name: "Add KPI" })).not.toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and editors can document KPIs.")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Suggested/ })).not.toBeInTheDocument();
    expect(screen.queryByText("To review")).not.toBeInTheDocument();
    expect(requests.find((r) => r.path === KPIS)?.query.review).toBe("catalog");
  });

  it("lists every KPI of the Workspace on the Data Warehouse page, in one request", async () => {
    const dwKpi = { ...NIM, id: DW_KPI_ID, name: "Customer count", source_system_id: null };
    const requests = renderApp(backend("viewer", { system: [], dw: [NIM, dwKpi] }), DW_FOLDER);

    const system = await screen.findByRole("row", { name: /Net interest margin/ });
    const dw = screen.getByRole("row", { name: /Customer count/ });
    await waitFor(() => expect(system).toHaveTextContent("Core Banking"));
    expect(dw).toHaveTextContent("Data Warehouse");
    expect(screen.getByRole("button", { name: "What is documented under?" })).toBeInTheDocument();
    const lists = requests.filter((r) => r.path === KPIS);
    expect(lists).toHaveLength(1);
    expect(lists[0]!.query.data_warehouse).toBeUndefined();
    expect(lists[0]!.query.source_system_id).toBeUndefined();
    // viewers: the KPIs tab only, but the filter is theirs too
    expect(screen.queryByRole("tab", { name: /Suggested/ })).not.toBeInTheDocument();
    expect(lists[0]!.query.rule_suggestions).toBeUndefined();
    const filter = screen.getByRole("combobox", { name: "Documented under" });
    fireEvent.change(filter, { target: { value: "dw" } });
    expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument();
    fireEvent.change(filter, { target: { value: CBS.id } });
    expect(screen.getByRole("row", { name: /Net interest margin/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Customer count/ })).not.toBeInTheDocument();
  });

  it("combines the system filter with the origin chips and the search", async () => {
    const dwAi = { ...AI_KPI, id: DW_KPI_ID, name: "Cross-sell ratio", source_system_id: null };
    renderApp(backend("editor", { system: [], dw: [NIM, AI_KPI, dwAi] }), DW_FOLDER);

    await screen.findByRole("row", { name: /Net interest margin/ });
    fireEvent.change(screen.getByRole("combobox", { name: "Documented under" }), {
      target: { value: "dw" },
    });
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    expect(screen.getByRole("row", { name: /Cross-sell ratio/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Cost to income/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search KPIs" }), {
      target: { value: "nothing like this" },
    });
    expect(screen.getByText("No matches")).toBeInTheDocument();
  });

  it("gives a Source System's page no system filter, no Documented under and no Rule chip", async () => {
    renderApp(backend("editor", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    await screen.findByRole("row", { name: /Net interest margin/ });
    expect(screen.queryByRole("combobox", { name: "Documented under" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rule" })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Documented under/ })).not.toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    renderApp(backend("viewer", { system: [], dw: [] }), SYSTEM_FOLDER);

    expect(await screen.findByText(/No KPIs yet/)).toBeInTheDocument();
  });

  it("shows an editor the review tabs with counts and the stat cards", async () => {
    renderApp(
      backend("editor", { system: [NIM, AI_KPI, SUGGESTED, REJECTED], dw: [] }),
      SYSTEM_FOLDER,
    );

    const kpisTab = await screen.findByRole("tab", { name: /^KPIs/ });
    await waitFor(() => expect(kpisTab).toHaveTextContent("2"));
    expect(screen.getByRole("tab", { name: /^Suggested/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /^Accepted/ })).toHaveTextContent("1");
    expect(screen.getByRole("tab", { name: /^Rejected/ })).toHaveTextContent("1");
    expect(screen.getByText("To review")).toBeInTheDocument();
    expect(screen.getByText("1 added by hand")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("row", { name: /Cost to income/ })).toHaveTextContent(
        "Accepted by Ada Lovelace",
      ),
    );
    expect(screen.queryByRole("row", { name: /Dormant account rate/ })).not.toBeInTheDocument();
  });

  it("filters by origin and searches the name, definition and owner", async () => {
    renderApp(backend("editor", { system: [NIM, AI_KPI], dw: [] }), SYSTEM_FOLDER);

    await screen.findByRole("row", { name: /Cost to income/ });
    fireEvent.click(screen.getByRole("button", { name: "AI" }));
    expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "Search KPIs" }), {
      target: { value: "operating cost" },
    });
    expect(screen.getByRole("row", { name: /Cost to income/ })).toBeInTheDocument();
    expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument();
  });

  it("accepts a suggestion from the Suggested tab", async () => {
    const requests = renderApp(
      backend("editor", { system: [NIM, SUGGESTED], dw: [] }, (r) =>
        r.method === "POST" && r.path === `${KPIS}/${SUGGESTED.id}/accept`
          ? json({ ...SUGGESTED, review_state: "accepted", decision: "accepted" })
          : undefined,
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.mouseDown(await screen.findByRole("tab", { name: /^Suggested/ }));
    const row = await screen.findByRole("row", { name: /Dormant account rate/ });
    expect(row).toHaveTextContent("accounts.last_txn_date measures dormancy.");
    expect(screen.getByRole("columnheader", { name: /Feasibility/ })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Status/ })).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Accept" }));

    expect(
      await screen.findByText("Dormant account rate accepted. It is now in KPIs."),
    ).toBeInTheDocument();
    expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/accept"))).toBe(true);
  });

  it("rejects a suggestion, and accepts a rejected one after all", async () => {
    const requests = renderApp(
      backend("editor", { system: [SUGGESTED, REJECTED], dw: [] }, (r) =>
        r.method === "POST" ? json({ ...SUGGESTED, review_state: "rejected" }) : undefined,
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.mouseDown(await screen.findByRole("tab", { name: /^Suggested/ }));
    const row = await screen.findByRole("row", { name: /Dormant account rate/ });
    fireEvent.click(within(row).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Dormant account rate rejected.")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("tab", { name: /^Rejected/ }));
    const rejected = await screen.findByRole("row", { name: /Branch cash utilisation/ });
    await waitFor(() => expect(rejected).toHaveTextContent("Rejected by Ada Lovelace"));
    fireEvent.click(within(rejected).getByRole("button", { name: "Accept after all" }));

    await waitFor(() =>
      expect(requests.some((r) => r.path === `${KPIS}/${REJECTED.id}/accept`)).toBe(true),
    );
    expect(requests.some((r) => r.path === `${KPIS}/${SUGGESTED.id}/reject`)).toBe(true);
  });

  it("opens an AI KPI's details in a side panel with its rationale and actions", async () => {
    renderApp(backend("editor", { system: [NIM, AI_KPI], dw: [] }), SYSTEM_FOLDER);

    const ai = await screen.findByRole("row", { name: /Cost to income/ });
    expect(ai).toHaveTextContent("AI");
    fireEvent.click(within(ai).getByRole("button", { name: "Cost to income" }));

    const panel = await screen.findByRole("dialog", { name: "Cost to income" });
    expect(within(panel).getByLabelText("AI rationale")).toHaveTextContent(
      "The ledger holds cost and income columns.",
    );
    expect(
      within(panel).getByText("Not yet. The AI proposes it once the Data Warehouse exists."),
    ).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Delete" })).toBeInTheDocument();
  });

  it("shows a viewer a KPI's details read-only", async () => {
    renderApp(backend("viewer", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Net interest margin" }));

    const details = await screen.findByRole("dialog", { name: "Net interest margin" });
    expect(within(details).getByText("Interest earned minus interest paid")).toBeInTheDocument();
    expect(within(details).getByText("FY2027: 3.2%")).toBeInTheDocument();
    expect(within(details).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("explains a column in a tooltip on keyboard focus", async () => {
    renderApp(backend("editor", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const info = await screen.findByRole("button", { name: "What is formula?" });
    fireEvent.focus(info);

    expect(
      (await screen.findAllByText(/Only the formula in words. SQL can be added once/)).length,
    ).toBeGreaterThan(0);
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

    const form = await openAddKpi();
    fireEvent.change(within(form).getByLabelText("Name"), {
      target: { value: "Net interest margin" },
    });
    fireEvent.change(within(form).getByLabelText(/^Unit/), { target: { value: "%" } });
    fireEvent.change(within(form).getByLabelText("Target label"), { target: { value: "FY2027" } });
    fireEvent.change(within(form).getByLabelText("Target value"), { target: { value: "3.2%" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add target" }));
    // The second row is left empty, so it is ignored; the third is filled.
    fireEvent.click(within(form).getByRole("button", { name: "Add target" }));
    const labels = within(form).getAllByLabelText("Target label");
    const values = within(form).getAllByLabelText("Target value");
    fireEvent.change(labels[2]!, { target: { value: "FY2028" } });
    fireEvent.change(values[2]!, { target: { value: "3.4%" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));

    expect(await screen.findByText("Net interest margin added.")).toBeInTheDocument();
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

  it("adds a cross-system KPI under the Data Warehouse, with no Source System", async () => {
    const requests = renderApp(
      backend("owner", { system: [], dw: [] }, (r) =>
        r.method === "POST" && r.path === KPIS
          ? json({ ...NIM, source_system_id: null }, 201)
          : undefined,
      ),
      DW_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Add cross-system KPI" }));
    const form = await screen.findByRole("dialog", { name: "Add cross-system KPI" });
    expect(
      within(form).getByText(
        "Document a KPI that needs more than one Source System: what it means and how it is calculated.",
      ),
    ).toBeInTheDocument();
    expect(form).toHaveTextContent("Documented under Data Warehouse");
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Customer count" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add cross-system KPI" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toMatchObject({
      name: "Customer count",
      source_system_id: null,
    });
  });

  it("refuses a target with only a label or a value without asking the server", async () => {
    const requests = renderApp(backend("editor", { system: [], dw: [] }), SYSTEM_FOLDER);

    const form = await openAddKpi();
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "X" } });
    fireEvent.change(within(form).getByLabelText("Target label"), { target: { value: "FY2027" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Each target needs both a label and a value.",
    );
    expect(requests.some((r) => r.method === "POST")).toBe(false);
  });

  it("shows the folder, the sections and the optional marks", async () => {
    renderApp(backend("editor", { system: [], dw: [] }), SYSTEM_FOLDER);

    const form = await openAddKpi();
    expect(form).toHaveTextContent("Documented under");
    expect(form).toHaveTextContent(CBS.name);
    for (const heading of ["What it measures", "How it is calculated", "Ownership", "Targets"]) {
      expect(within(form).getByRole("heading", { name: new RegExp(heading) })).toBeInTheDocument();
    }
    expect(within(form).getByLabelText(/^Aggregation/)).toBeInTheDocument();
    expect(within(form).getAllByLabelText("Target label")).toHaveLength(1);
    expect(within(form).queryByLabelText("Status")).not.toBeInTheDocument();
  });

  it("offers the known values with Other… last, and the units grouped", async () => {
    renderApp(backend("editor", { system: [], dw: [] }), SYSTEM_FOLDER);

    const form = await openAddKpi();
    const names = (label: RegExp) =>
      within(within(form).getByLabelText(label))
        .getAllByRole("option")
        .map((o) => o.textContent);
    expect(names(/^Aggregation/)).toEqual([
      "Choose…", "Sum", "Average", "Count", "Count distinct", "Minimum", "Maximum", "Ratio",
      "Percentage", "Growth (period over period)", "Other…",
    ]);
    expect(names(/^Unit/).slice(0, 6)).toEqual(["Choose…", "SAR", "USD", "EUR", "EGP", "%"]);
    expect(names(/^Unit/).at(-1)).toBe("Other…");
    expect(names(/^Refresh frequency/)).toEqual([
      "Choose…", "Real time", "Hourly", "Daily", "Weekly", "Monthly", "Quarterly", "Yearly",
      "On demand", "Other…",
    ]);
  });

  it("saves an Other… value, and blocks an empty one", async () => {
    const requests = renderApp(
      backend("editor", { system: [], dw: [] }, (r) =>
        r.method === "POST" && r.path === KPIS ? json(NIM, 201) : undefined,
      ),
      SYSTEM_FOLDER,
    );

    const form = await openAddKpi();
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "X" } });
    expect(within(form).queryByLabelText("Other aggregation")).not.toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText(/^Aggregation/), { target: { value: "__other" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Type the aggregation, or choose one from the list.",
    );
    expect(requests.some((r) => r.method === "POST")).toBe(false);

    fireEvent.change(within(form).getByLabelText("Other aggregation"), {
      target: { value: "Median" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add KPI" }));
    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")?.body).toMatchObject({ aggregation: "Median" });
  });

  it("suggests the owners already on the page", async () => {
    renderApp(backend("editor", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const form = await openAddKpi();
    const owner = within(form).getByLabelText(/^Owner/);
    const list = document.getElementById(owner.getAttribute("list") ?? "");
    expect([...(list?.querySelectorAll("option") ?? [])].map((o) => o.getAttribute("value"))).toEqual(
      ["CFO office"],
    );
  });

  it("removes a target row", async () => {
    renderApp(backend("editor", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const form = await openEditKpi();
    expect(within(form).getByLabelText("Target label")).toHaveValue("FY2027");
    fireEvent.click(within(form).getByRole("button", { name: "Remove target" }));
    expect(within(form).queryByLabelText("Target label")).not.toBeInTheDocument();
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

    const form = await openEditKpi();
    fireEvent.change(within(form).getByLabelText("Status"), { target: { value: "in_review" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `${KPIS}/${NIM.id}`,
      body: { version: 1, status: "in_review", name: "Net interest margin" },
    });
  });

  it("opens a stored value outside the lists under Other…", async () => {
    renderApp(backend("editor", { system: [NIM], dw: [] }), SYSTEM_FOLDER);

    const form = await openEditKpi();
    // NIM stores "ratio" and "monthly", which are not the list's exact spellings.
    expect(within(form).getByLabelText(/^Aggregation/)).toHaveValue("__other");
    expect(within(form).getByLabelText("Other aggregation")).toHaveValue("ratio");
    expect(within(form).getByLabelText(/^Unit/)).toHaveValue("%");
    expect(within(form).getByLabelText("Other refresh frequency")).toHaveValue("monthly");
    expect(within(form).getByLabelText("Status")).toBeInTheDocument();
  });

  it("tells the editor when someone else changed the KPI first", async () => {
    renderApp(
      backend("owner", { system: [NIM], dw: [] }, (r) =>
        r.method === "PATCH"
          ? apiError(409, "version_conflict", "Someone else changed this KPI.")
          : undefined,
      ),
      SYSTEM_FOLDER,
    );

    const form = await openEditKpi();
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Someone else changed this KPI since you opened it.",
    );
  });

  it("says an accepted suggestion moves to Rejected when deleted", async () => {
    renderApp(
      backend("editor", { system: [AI_KPI], dw: [] }, (r) =>
        r.method === "DELETE" ? noContent() : undefined,
      ),
      SYSTEM_FOLDER,
    );

    const row = await screen.findByRole("row", { name: /Cost to income/ });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Cost to income?" });
    expect(dialog).toHaveTextContent(
      /came from a suggestion. It moves to Rejected as “Removed by you”/,
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete KPI" }));

    expect(await screen.findByText("Cost to income moved to Rejected.")).toBeInTheDocument();
  });

  it("deletes a KPI added by hand for good", async () => {
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

    const row = await screen.findByRole("row", { name: /Net interest margin/ });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Net interest margin?" });
    expect(dialog).toHaveTextContent("This KPI was added by hand. It is removed for good.");
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete KPI" }));

    await waitFor(() =>
      expect(screen.queryByRole("row", { name: /Net interest margin/ })).not.toBeInTheDocument(),
    );
    expect(requests.some((r) => r.method === "DELETE" && r.path === `${KPIS}/${NIM.id}`)).toBe(
      true,
    );
    expect(await screen.findByText(/No KPIs yet/)).toBeInTheDocument();
  });
});

describe("rule suggestions on the Data Warehouse page", () => {
  const ruleBackend = (handle?: (r: ApiRequest) => Response | undefined, kpis: Kpi[] = []) =>
    backend("editor", { system: [], dw: kpis }, handle, {
      open: [RULE],
      rejected: [RULE_REJECTED],
    });
  const acceptedRule = (r: ApiRequest) =>
    r.method === "POST" && r.path === `${KPIS}/suggestions/accept`
      ? json({ ...NIM, source_system_id: null, origin: "rule" }, 201)
      : undefined;

  it("lists them in Suggested and Rejected with origin Rule under the Data Warehouse", async () => {
    const requests = renderApp(ruleBackend(), DW_FOLDER);

    const suggested = await screen.findByRole("tab", { name: /^Suggested/ });
    await waitFor(() => expect(suggested).toHaveTextContent("1"));
    fireEvent.mouseDown(suggested);
    const row = await screen.findByRole("row", { name: /Sum of amount \(fact_loan\)/ });
    expect(row).toHaveTextContent("Rule");
    expect(row).toHaveTextContent("Data Warehouse");
    expect(row).toHaveTextContent("Needs mapping");
    fireEvent.click(within(row).getByRole("button", { name: "Sum of amount (fact_loan)" }));
    const panel = await screen.findByRole("dialog", { name: "Sum of amount (fact_loan)" });
    expect(within(panel).getByText(/SELECT SUM\(amount\)/)).toBeInTheDocument();
    expect(within(panel).getByText("core.fact_loan.amount")).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));

    fireEvent.mouseDown(screen.getByRole("tab", { name: /^Rejected/ }));
    const rejected = await screen.findByRole("row", { name: /Average of fee/ });
    await waitFor(() => expect(rejected).toHaveTextContent("Rejected by Ada Lovelace"));
    // one request for the page, and none for a suggestion's links
    expect(requests.filter((r) => r.path === KPIS)).toHaveLength(1);
    expect(requests.some((r) => r.path.includes("/links"))).toBe(false);
  });

  it("accepts one: it is created by its key", async () => {
    const requests = renderApp(ruleBackend(acceptedRule), DW_FOLDER);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: /^Suggested/ }));
    const row = await screen.findByRole("row", { name: /Sum of amount/ });
    fireEvent.click(within(row).getByRole("button", { name: "Accept" }));

    expect(
      await screen.findByText("Sum of amount (fact_loan) accepted. It is now in KPIs."),
    ).toBeInTheDocument();
    expect(requests.find((r) => r.path === `${KPIS}/suggestions/accept`)?.body).toEqual({
      key: RULE.key,
    });
  });

  it("rejects one, and accepts a rejected one after all", async () => {
    const requests = renderApp(
      ruleBackend((r) =>
        r.method === "POST" && r.path === `${KPIS}/suggestions/dismiss`
          ? noContent()
          : acceptedRule(r),
      ),
      DW_FOLDER,
    );

    fireEvent.mouseDown(await screen.findByRole("tab", { name: /^Suggested/ }));
    const row = await screen.findByRole("row", { name: /Sum of amount/ });
    fireEvent.click(within(row).getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Sum of amount (fact_loan) rejected.")).toBeInTheDocument();
    expect(requests.find((r) => r.path === `${KPIS}/suggestions/dismiss`)?.body).toEqual({
      key: RULE.key,
    });

    fireEvent.mouseDown(screen.getByRole("tab", { name: /^Rejected/ }));
    const rejected = await screen.findByRole("row", { name: /Average of fee/ });
    fireEvent.click(within(rejected).getByRole("button", { name: "Accept after all" }));
    await waitFor(() =>
      expect(requests.find((r) => r.path === `${KPIS}/suggestions/accept`)?.body).toEqual({
        key: RULE_REJECTED.key,
      }),
    );
  });

  it("moves an accepted rule KPI to Rejected when deleted", async () => {
    const accepted: Kpi = {
      ...NIM,
      id: DW_KPI_ID,
      source_system_id: null,
      name: "Sum of fee (fact_loan)",
      origin: "rule",
      review_state: "accepted",
      decision: "accepted",
    };
    const requests = renderApp(
      ruleBackend((r) => (r.method === "DELETE" ? noContent() : undefined), [accepted]),
      DW_FOLDER,
    );

    const row = await screen.findByRole("row", { name: /Sum of fee/ });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete Sum of fee (fact_loan)?" });
    expect(dialog).toHaveTextContent("It moves to Rejected as “Removed by you”");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete KPI" }));

    expect(
      await screen.findByText("Sum of fee (fact_loan) moved to Rejected."),
    ).toBeInTheDocument();
    expect(requests.some((r) => r.method === "DELETE" && r.path === `${KPIS}/${DW_KPI_ID}`)).toBe(
      true,
    );
  });
});

describe("suggesting KPIs with the AI", () => {
  const JOBS = `/api/v1/workspaces/${WORKSPACE_ID}/systems/${CBS.id}/kpi-suggestions`;
  const job = (fields: Partial<KpiSuggestionJob>): KpiSuggestionJob => ({
    job_id: "99999999-9999-4999-8999-999999999999",
    status: "running",
    error: null,
    created: null,
    dropped: null,
    skipped: null,
    created_at: "2026-01-05T09:00:00Z",
    finished_at: null,
    ...fields,
  });

  /** The job endpoints: GET answers `before` until a job starts, then `after`. */
  function jobs(before: KpiSuggestionJob | null, after: KpiSuggestionJob, onEnd?: () => void) {
    let started = false;
    return (r: ApiRequest) => {
      if (r.path !== JOBS) {
        return undefined;
      }
      if (r.method === "POST") {
        started = true;
        return json(job({}), 202);
      }
      if (started) {
        onEnd?.();
      }
      return json({ job: started ? after : before });
    };
  }

  it("shows owners and editors the button, never viewers", async () => {
    renderApp(backend("viewer", { system: [NIM], dw: [] }), SYSTEM_FOLDER);
    await screen.findByRole("row", { name: /Net interest margin/ });
    expect(screen.queryByRole("button", { name: "Suggest KPIs" })).not.toBeInTheDocument();
  });

  it("disables the button while a job runs and says what the AI reads", async () => {
    renderApp(
      backend("editor", { system: [NIM, REJECTED], dw: [] }, jobs(job({}), job({}))),
      SYSTEM_FOLDER,
    );

    const button = await screen.findByRole("button", { name: "Suggesting…" });
    expect(button).toBeDisabled();
    const status = await screen.findByRole("status");
    await waitFor(() => expect(status).toHaveTextContent("Suggesting KPIs for Core Banking…"));
    expect(status).toHaveTextContent("the 2 KPIs already here (1 rejected)");
    expect(status).toHaveTextContent("You can leave this page; the job keeps running.");
  });

  it("reports new suggestions, counts the dropped ones and shows the Suggested tab", async () => {
    const system = [NIM];
    const requests = renderApp(
      backend(
        "editor",
        { system, dw: [] },
        jobs(null, job({ status: "succeeded", created: 1, dropped: 1, skipped: 0 }), () => {
          if (!system.includes(SUGGESTED)) {
            system.push(SUGGESTED);
          }
        }),
      ),
      SYSTEM_FOLDER,
    );

    const button = await screen.findByRole("button", { name: "Suggest KPIs" });
    fireEvent.click(button);

    const status = await screen.findByText("1 new KPI suggested");
    expect(status.closest("[role=status]")).toHaveTextContent(
      "They are in the Suggested tab for you to accept or reject. 1 more was dropped: based on tables outside Core Banking.",
    );
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Suggested/ })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(await screen.findByRole("row", { name: /Dormant account rate/ })).toBeInTheDocument();
    expect(requests.some((r) => r.method === "POST" && r.path === JOBS)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("1 new KPI suggested")).not.toBeInTheDocument();
  });

  it("says when there is nothing new to suggest, which is not an error", async () => {
    renderApp(
      backend(
        "editor",
        { system: [NIM, REJECTED], dw: [] },
        jobs(null, job({ status: "succeeded", created: 0, dropped: 0, skipped: 0 })),
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Suggest KPIs" }));

    const title = await screen.findByText("No new KPIs to suggest");
    expect(title.closest("[role=status]")).toHaveTextContent(
      "The AI found nothing that differs from the 2 KPIs already here, including rejected ones.",
    );
    expect(screen.getByRole("tab", { name: /^KPIs/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Suggest KPIs" })).toBeEnabled();
  });

  it("says why a job failed", async () => {
    renderApp(
      backend(
        "editor",
        { system: [NIM], dw: [] },
        jobs(null, job({ status: "failed", error: "No model is assigned." })),
      ),
      SYSTEM_FOLDER,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Suggest KPIs" }));

    const title = await screen.findByText("Suggesting KPIs failed");
    expect(title.closest("[role=status]")).toHaveTextContent("No model is assigned.");
  });

  it("shows a job that ended while the page was closed until it is dismissed", async () => {
    const ended = job({ status: "succeeded", created: 1, dropped: 1, skipped: 0 });
    const open = () =>
      renderApp(
        backend("editor", { system: [NIM, SUGGESTED], dw: [] }, jobs(ended, ended)),
        SYSTEM_FOLDER,
      );
    open();

    const title = await screen.findByText("1 new KPI suggested");
    expect(title.closest("[role=status]")).toHaveTextContent(
      "1 more was dropped: based on tables outside Core Banking.",
    );
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /^Suggested/ })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("1 new KPI suggested")).not.toBeInTheDocument();

    cleanup();
    open();
    await screen.findByRole("button", { name: "Suggest KPIs" });
    await screen.findByRole("tab", { name: /^Suggested/ });
    expect(screen.queryByText("1 new KPI suggested")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^KPIs/ })).toHaveAttribute("aria-selected", "true");
  });

  it("disables the button in an archived Workspace", async () => {
    const live = backend("editor", { system: [NIM], dw: [] }, jobs(null, job({})));
    renderApp(
      (r) =>
        r.method === "GET" && r.path === `/api/v1/workspaces/${WORKSPACE_ID}`
          ? json({ ...workspace("editor"), status: "archived", archived_at: "2026-02-01T09:00:00Z" })
          : live(r),
      SYSTEM_FOLDER,
    );

    expect(await screen.findByRole("button", { name: "Suggest KPIs" })).toBeDisabled();
  });

  it("lists the tables a suggestion is based on in its details", async () => {
    renderApp(
      backend("editor", {
        system: [{ ...SUGGESTED, based_on: ["core.accounts", "core.transactions"] }],
        dw: [],
      }),
      SYSTEM_FOLDER,
    );

    fireEvent.mouseDown(await screen.findByRole("tab", { name: /^Suggested/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Dormant account rate" }));

    const panel = await screen.findByRole("dialog", { name: "Dormant account rate" });
    const list = within(panel).getByText("Based on").parentElement!;
    expect(within(list).getByText("core.accounts")).toBeInTheDocument();
    expect(within(list).getByText("core.transactions")).toBeInTheDocument();
  });

  describe("cross-system KPIs on the Data Warehouse page", () => {
    const DW_JOBS = `/api/v1/workspaces/${WORKSPACE_ID}/data-warehouse/kpi-suggestions`;
    const ready = { available: true, source_systems: 2, dw_model: false };

    /** The DW job endpoints: GET answers `before` until a job starts, then `after`. */
    function dwJobs(
      before: KpiSuggestionJob | null,
      after: KpiSuggestionJob,
      readiness: object = ready,
    ) {
      let started = false;
      return (r: ApiRequest) => {
        if (r.path !== DW_JOBS) {
          return undefined;
        }
        if (r.method === "POST") {
          started = true;
          return json(job({}), 202);
        }
        return json({ job: started ? after : before, ...readiness });
      };
    }

    it("is unavailable, still focusable, with a tooltip saying why", async () => {
      const requests = renderApp(
        backend(
          "editor",
          { system: [], dw: [NIM] },
          dwJobs(null, job({}), { available: false, source_systems: 1, dw_model: false }),
        ),
        DW_FOLDER,
      );

      const button = await screen.findByRole("button", { name: "Suggest cross-system KPIs" });
      await waitFor(() => expect(button).toHaveAttribute("aria-disabled", "true"));
      expect(button).toBeEnabled();
      fireEvent.focus(button);
      expect((await screen.findAllByText("Not available yet")).length).toBeGreaterThan(0);
      expect(
        screen.getAllByText(
          "Cross-system KPIs need at least two Source Systems or a Data Warehouse model. This Workspace has one Source System and no DW model yet.",
        ).length,
      ).toBeGreaterThan(0);
      fireEvent.click(button);
      expect(requests.some((r) => r.method === "POST" && r.path === DW_JOBS)).toBe(false);
    });

    it("says what the AI reads while it runs", async () => {
      renderApp(
        backend("editor", { system: [], dw: [NIM, REJECTED] }, dwJobs(job({}), job({}))),
        DW_FOLDER,
      );

      expect(await screen.findByRole("button", { name: "Suggesting…" })).toBeDisabled();
      const status = await screen.findByText(
        "Suggesting cross-system KPIs for the Data Warehouse…",
      );
      expect(status.closest("[role=status]")).toHaveTextContent(
        "The AI reads a summary of each Source System, the relationships between them, the DW model and all 2 KPIs in the Workspace (1 rejected). It only suggests KPIs that need more than one system or the DW model.",
      );
    });

    it("reports new suggestions and the ones that used only one Source System", async () => {
      const requests = renderApp(
        backend(
          "editor",
          { system: [], dw: [NIM] },
          dwJobs(null, job({ status: "succeeded", created: 2, dropped: 1, skipped: 0 })),
        ),
        DW_FOLDER,
      );

      fireEvent.click(await screen.findByRole("button", { name: "Suggest cross-system KPIs" }));

      const title = await screen.findByText("2 new KPIs suggested");
      expect(title.closest("[role=status]")).toHaveTextContent(
        "They are in the Suggested tab for you to accept or reject. 1 more was dropped: it used only one Source System.",
      );
      expect(requests.some((r) => r.method === "POST" && r.path === DW_JOBS)).toBe(true);
    });

    it("says when there is nothing new, or why it failed", async () => {
      renderApp(
        backend(
          "editor",
          { system: [], dw: [NIM] },
          dwJobs(null, job({ status: "succeeded", created: 0, dropped: 0, skipped: 0 })),
        ),
        DW_FOLDER,
      );
      fireEvent.click(await screen.findByRole("button", { name: "Suggest cross-system KPIs" }));
      expect(await screen.findByText("No new KPIs to suggest")).toBeInTheDocument();

      cleanup();
      renderApp(
        backend(
          "editor",
          { system: [], dw: [NIM] },
          dwJobs(null, job({ status: "failed", error: "No model is assigned." })),
        ),
        DW_FOLDER,
      );
      fireEvent.click(await screen.findByRole("button", { name: "Suggest cross-system KPIs" }));
      const failed = await screen.findByText("Suggesting cross-system KPIs failed");
      expect(failed.closest("[role=status]")).toHaveTextContent("No model is assigned.");
    });
  });
});
