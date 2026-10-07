import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { SourceSummary } from "../api/sourceSummary";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { apiError, json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const VIEWER: Workspace = {
  id: WORKSPACE_ID,
  name: "Retail DW",
  description: "",
  domain: "",
  role: "viewer",
  permissions: ["workspace.view"],
  status: "active",
  archived_at: null,
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

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

const SUMMARY: SourceSummary = {
  system_id: CBS.id,
  has_snapshot: true,
  table_count: 40,
  documented_tables: 10,
  documented_pct: 25,
  profiled_tables: 30,
  profiled_pct: 75,
  relationships_found: 12,
  relationships_accepted: 5,
  relationships_to_review: 7,
  pii_found: 4,
  pii_confirmed: 1,
  pii_to_review: 3,
  status: "in_progress",
};

function backend(summary: () => Response) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(VIEWER);
      case `GET ${API}/progress`:
        return json({
          source_analysis: [{ system_id: CBS.id, name: CBS.name, status: "in_progress" }],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${API}/systems/${CBS.id}/summary`:
        return summary();
      default:
        return undefined;
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${CBS.id}`;

describe("the Source System dashboard", () => {
  it("shows the table count, documented and profiled shares, relationships and PII", async () => {
    renderApp(backend(() => json(SUMMARY)), FOLDER);

    const dashboard = await screen.findByRole("region", { name: "Source summary" });
    await within(dashboard).findByText("Tables");

    const figure = (label: string) => {
      const term = within(dashboard).getByText(label);
      return term.nextElementSibling?.textContent;
    };
    expect(figure("Tables")).toBe("40");
    expect(figure("Documented")).toBe("25% (10 of 40)");
    expect(figure("Profiled")).toBe("75% (30 of 40)");
    expect(figure("Relationships found")).toBe("12 (7 to review)");
    expect(figure("PII found")).toBe("4 (3 to review)");
    expect(within(dashboard).getByText("In progress")).toBeInTheDocument();
  });

  it("says so before the first Snapshot", async () => {
    renderApp(
      backend(() => json({ ...SUMMARY, has_snapshot: false, table_count: 0, status: "not_started" })),
      FOLDER,
    );

    const dashboard = await screen.findByRole("region", { name: "Source summary" });

    expect(
      await within(dashboard).findByText(/No Snapshot yet/),
    ).toBeInTheDocument();
  });

  it("reports a failure to load", async () => {
    renderApp(backend(() => apiError(500, "internal", "Boom")), FOLDER);

    expect(await screen.findByText(/Could not load the source summary/)).toBeInTheDocument();
  });

  it("shows each system's Source Analysis in the stage progress panel", async () => {
    renderApp(backend(() => json(SUMMARY)), FOLDER);

    const item = await screen.findByText("Source Analysis: Core Banking");

    expect(item.parentElement).toHaveTextContent("In progress");
  });
});
