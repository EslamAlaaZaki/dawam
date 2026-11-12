import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { PiiColumn } from "../api/pii";
import type { SourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
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
  status: Workspace["status"] = "active",
): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Bank DW",
    description: "",
    domain: "",
    role,
    permissions,
    status,
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const OWNER = workspace("owner", [
  "workspace.view",
  "pii.review",
  "pii.view_review",
  "source_system.profile",
]);
const VIEWER = workspace("viewer", ["workspace.view"]);

const CRM: SourceSystem = {
  id: "33333333-3333-4333-8333-333333333333",
  workspace_id: WORKSPACE_ID,
  name: "CRM",
  code: "crm",
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};
const SYSTEM = `${API}/systems/${CRM.id}`;
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CRM.id}/pii`;

function table(id: string, name: string, kind: "table" | "view") {
  return {
    id,
    db_schema: "crm",
    name,
    kind,
    status: "present" as const,
    view_definition: null,
    row_estimate: null,
    comment: null,
    columns: [],
    constraints: [],
    indexes: [],
  };
}

const SCHEMA = {
  id: "55555555-5555-4555-8555-555555555555",
  source_system_id: CRM.id,
  origin: "connection",
  job_id: null,
  taken_at: "2026-01-05T10:00:00Z",
  is_latest: true,
  schema_count: 1,
  table_count: 3,
  column_count: 0,
  routine_count: 0,
  db_schemas: [],
  tables: [
    table("t1", "contacts", "table"),
    table("t2", "complaints", "table"),
    table("t3", "v_active", "view"),
  ],
  routines: [],
  removed_columns: [],
  removed_tables: [],
} as unknown as SourceSchema;

const EDITOR_ID = "9e7f2d2f-2a54-4d2c-8c6f-6b2f3c4d5e6f";

type Rule = PiiColumn["rules"][number];

function rule(id: string, name: string, extra: Partial<Rule> = {}): Rule {
  return {
    id,
    source: "name",
    rule: name,
    category: "direct_identifier",
    confidence: 0.92,
    evidence: `The column name matches the name rule ${name}.`,
    status: "suggested",
    ...extra,
  };
}

function column(id: string, name: string, extra: Partial<PiiColumn> = {}): PiiColumn {
  return {
    id,
    table_id: "t1",
    db_schema: "crm",
    table: "contacts",
    column: name,
    data_type: "varchar(20)",
    source_status: "present",
    state: "accepted",
    category: "direct_identifier",
    is_sensitive: true,
    rules: [],
    manual: false,
    decision: "accepted",
    decided_by: EDITOR_ID,
    decided_at: "2026-01-06T10:00:00Z",
    rejected_before_by: null,
    rejected_before_at: null,
    hashed_mapping: null,
    table_profiled_at: "2026-01-04T10:00:00Z",
    ...extra,
  };
}

const ACCEPTED = column("c1", "national_id", {
  rules: [
    rule("f1", "national_id", { status: "accepted" }),
    rule("f2", "saudi_national_id", {
      source: "value",
      confidence: 0.81,
      status: "accepted",
      evidence: "81 of 100 sampled values pass the checksum",
    }),
  ],
});
const HAND = column("c2", "notes", { manual: true, decision: "added", category: "sensitive" });
const HASHED = column("c3", "iban", {
  category: "financial",
  rules: [rule("f3", "iban", { status: "accepted", category: "financial" })],
  hashed_mapping: "stg_crm_contacts.iban",
});
const SUGGESTED = column("c4", "email", {
  state: "suggested",
  is_sensitive: false,
  category: "quasi_identifier",
  decision: null,
  decided_by: null,
  decided_at: null,
  rejected_before_by: EDITOR_ID,
  rejected_before_at: "2026-01-02T10:00:00Z",
  rules: [
    rule("f4", "email", { category: "quasi_identifier", confidence: 0.9 }),
    rule("f5", "custom:mail", { category: "sensitive", confidence: 0.6, source: "value" }),
  ],
});
const REJECTED = column("c5", "phone", {
  state: "rejected",
  is_sensitive: false,
  category: "quasi_identifier",
  decision: "removed",
  rules: [rule("f6", "phone", { status: "rejected", category: "quasi_identifier" })],
});

const TAB_ITEMS: Record<string, PiiColumn[]> = {
  pii: [ACCEPTED, HAND, HASHED],
  suggested: [SUGGESTED],
  accepted: [ACCEPTED, HASHED],
  rejected: [REJECTED],
};

interface Requests {
  scans: unknown[];
  decisions: { path: string; body: unknown }[];
}

const fresh = (): Requests => ({ scans: [], decisions: [] });

function backend(ws: Workspace, requests: Requests) {
  return (r: ApiRequest) => {
    const key = `${r.method} ${r.path}`;
    switch (key) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(ws);
      case `GET ${API}/members`:
        return json({ items: [{ user_id: EDITOR_ID, display_name: "Grace Hopper" }] });
      case `GET ${API}/systems`:
        return json({ items: [CRM], next_cursor: null });
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${SYSTEM}/schema`:
        return json(SCHEMA);
      case `GET ${SYSTEM}/pii-columns`:
        return json({
          items: TAB_ITEMS[r.query.tab ?? "pii"],
          counts: { pii: 3, suggested: 1, accepted: 2, rejected: 1 },
          can_profile: true,
        });
      case `POST ${SYSTEM}/pii-scans`:
        requests.scans.push(r.body);
        return json({ job_id: "j1", status: "queued" }, 202);
    }
    const decision = key.match(/^POST .*\/pii-columns\/(c\d)\/([a-z-]+)$/);
    if (decision) {
      requests.decisions.push({ path: `${decision[1]} ${decision[2]}`, body: r.body });
      if (decision[2] === "remove") {
        return json({ column: null, profile_job_id: null });
      }
      return json(SUGGESTED);
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

/** The row of a column (its name comes before any rule of the same name). */
const rowOf = async (name: string) => (await screen.findAllByText(name))[0]!.closest("tr")!;

describe("the PII folder", () => {
  it("lists the PII columns with their rules, category, type and who decided", async () => {
    renderApp(backend(OWNER, fresh()), FOLDER);

    const row = await rowOf("national_id");
    expect(row).toHaveTextContent("crm.contacts");
    expect(row).toHaveTextContent("varchar(20)");
    expect(row).toHaveTextContent("Direct identifier");
    expect(row).toHaveTextContent("saudi_national_id");
    expect(row).toHaveTextContent("81%");
    expect(row).not.toHaveTextContent("sampled values"); // no evidence in the compact list
    expect(await within(row).findByText(/Accepted by Grace Hopper/)).toBeInTheDocument();
    expect(await rowOf("notes")).toHaveTextContent("Added by hand");
    expect(within(await rowOf("iban")).getByText("Hashed in staging")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Suggested/ })).toHaveTextContent("1");
  });

  it("filters the PII tab by where the rules come from", async () => {
    renderApp(backend(OWNER, fresh()), FOLDER);

    await rowOf("national_id");
    const filters = screen.getByRole("group", { name: "Filter by source" });
    expect(within(filters).getByRole("button", { name: /Value scan/ })).toHaveTextContent("1");
    fireEvent.click(within(filters).getByRole("button", { name: /Manual/ }));

    expect(screen.queryByText("national_id")).toBeNull();
    expect(screen.getByText("notes")).toBeInTheDocument();
  });

  it("says suggested columns are protected too and links to them", async () => {
    renderApp(backend(OWNER, fresh()), FOLDER);

    expect(await screen.findByText(/1 suggested column is also protected/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review suggested" }));

    expect(await rowOf("email")).toHaveTextContent("Rejected before by Grace Hopper");
  });

  it("accepts a suggested column with the most confident rule's category", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);
    await rowOf("national_id");
    openTab(/Suggested/);

    const row = await rowOf("email");
    expect(within(row).getByText("Rules disagree")).toBeInTheDocument();
    expect(within(row).getByRole("combobox", { name: "Category of email" })).toHaveTextContent(
      "Quasi-identifier",
    );
    fireEvent.click(within(row).getByRole("button", { name: "Accept" }));

    await waitFor(() =>
      expect(requests.decisions).toEqual([
        { path: "c4 accept", body: { category: "quasi_identifier" } },
      ]),
    );
  });

  it("rejects a suggested column in one click", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);
    await rowOf("national_id");
    openTab(/Suggested/);

    fireEvent.click(within(await rowOf("email")).getByRole("button", { name: "Reject" }));

    await waitFor(() => expect(requests.decisions.map((d) => d.path)).toEqual(["c4 reject"]));
  });

  it("accepts a rejected column after all through a dialog with a category", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);
    await rowOf("national_id");
    openTab(/Rejected/);

    const row = await rowOf("phone");
    expect(await within(row).findByText(/Removed by Grace Hopper/)).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Accept" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Accept as PII" }));

    await waitFor(() =>
      expect(requests.decisions).toEqual([
        { path: "c5 accept-rejected", body: { category: "quasi_identifier" } },
      ]),
    );
  });

  it("removes PII and profiles the table again when ticked", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);

    fireEvent.click(
      within(await rowOf("national_id")).getByRole("button", {
        name: "Remove PII from national_id",
      }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Moves to Rejected");
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
    expect(dialog).toHaveTextContent("Profile contacts again");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove PII" }));

    await waitFor(() =>
      expect(requests.decisions).toEqual([{ path: "c1 remove", body: { profile_again: true } }]),
    );
  });

  it("blocks removing PII while the staging mapping hashes the column", async () => {
    renderApp(backend(OWNER, fresh()), FOLDER);

    fireEvent.click(
      within(await rowOf("iban")).getByRole("button", { name: "Remove PII from iban" }),
    );
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toHaveTextContent("stg_crm_contacts.iban");
    expect(dialog).toHaveTextContent("Switch it back to direct in Mappings first");
    expect(within(dialog).queryByRole("button", { name: "Remove PII" })).toBeNull();
  });

  it("shows a viewer the PII tab only, read only", async () => {
    renderApp(backend(VIEWER, fresh()), FOLDER);

    expect(await rowOf("national_id")).toBeInTheDocument();
    expect(
      screen.getByText("Read only. Owners and Editors review and change PII."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("button", { name: "Scan values" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add PII column" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove PII/ })).toBeNull();
  });

  it("offers Add PII column on the PII tab only", async () => {
    renderApp(backend(OWNER, fresh()), FOLDER);

    expect(await screen.findByRole("button", { name: "Add PII column" })).toBeInTheDocument();
    openTab(/Suggested/);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Add PII column" })).toBeNull(),
    );
  });

  it("scans every table (not views) by default, and starts the job", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Scan values" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findAllByRole("checkbox")).toHaveLength(2);
    expect(within(dialog).queryByText("crm.v_active")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Start scan" }));

    await waitFor(() =>
      expect(requests.scans).toEqual([{ table_ids: ["t1", "t2"], sample_size: 1000 }]),
    );
  });

  it("narrows the scan to the tables ticked", async () => {
    const requests = fresh();
    renderApp(backend(OWNER, requests), FOLDER);

    fireEvent.click(await screen.findByRole("button", { name: "Scan values" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByRole("checkbox", { name: "crm.complaints" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Start scan" }));

    await waitFor(() => expect(requests.scans).toEqual([{ table_ids: ["t1"], sample_size: 1000 }]));
  });

  it("keeps the review tabs for owners in an archived Workspace, with no changes", async () => {
    const archived = workspace("owner", ["workspace.view", "pii.view_review"], "archived");
    renderApp(backend(archived, fresh()), FOLDER);

    expect(await rowOf("national_id")).toBeInTheDocument();
    expect(screen.getByText("Read only. This Workspace is archived.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Scan values" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add PII column" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove PII/ })).toBeNull();
    openTab(/Suggested/);
    const row = await rowOf("email");
    expect(within(row).queryByRole("button", { name: "Accept" })).toBeNull();
    expect(within(row).queryByRole("button", { name: "Reject" })).toBeNull();
    openTab(/Rejected/);
    expect(within(await rowOf("phone")).queryByRole("button", { name: "Accept" })).toBeNull();
  });
});
