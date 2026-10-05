import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Job } from "../api/jobs";
import type { Me } from "../api/queries";
import type { Workspace } from "../api/workspaces";
import { CSRF_TOKEN, json, renderApp, setCsrfCookie, type ApiRequest } from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};
const GRACE_ID = "22222222-2222-4222-8222-222222222222";

function workspace(role: Workspace["role"]): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: ["workspace.view"],
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

function job(overrides: Partial<Job>): Job {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    workspace_id: WORKSPACE_ID,
    kind: "profile",
    title: "Profile ERP",
    status: "running",
    progress: 40,
    log: "",
    error: null,
    created_by: ADA.id,
    created_at: "2026-01-05T10:00:00Z",
    started_at: "2026-01-05T10:00:01Z",
    finished_at: null,
    ...overrides,
  };
}

const MINE = job({ log: "2026-01-05T10:00:02Z reading tables\n" });
const GRACES = job({
  id: "44444444-4444-4444-8444-444444444444",
  title: "Scan CRM",
  status: "queued",
  progress: 0,
  created_by: GRACE_ID,
});
const FAILED = job({
  id: "55555555-5555-4555-8555-555555555555",
  title: "Export model",
  status: "failed",
  error: "the source refused the connection",
});

function backend(role: Workspace["role"]) {
  const jobs = [MINE, GRACES, FAILED];
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(role));
      case `GET ${API}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/jobs`:
        return json({ items: jobs, next_cursor: null });
      case `POST /api/v1/jobs/${MINE.id}/cancel`:
        jobs[0] = { ...MINE, status: "cancelled" };
        return json(jobs[0]);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("background jobs", () => {
  it("shows each job's status, progress, log and why it failed", async () => {
    renderApp(backend("editor"), PATH);

    const mine = await screen.findByRole("listitem", { name: "Profile ERP" });
    expect(within(mine).getByText("Running")).toBeInTheDocument();
    expect((within(mine).getByRole("progressbar") as HTMLProgressElement).value).toBe(40);
    expect(within(mine).getByText(/reading tables/)).toBeInTheDocument();
    const failed = screen.getByRole("listitem", { name: "Export model" });
    expect(within(failed).getByText("Failed")).toBeInTheDocument();
    expect(within(failed).getByText("the source refused the connection")).toBeInTheDocument();
  });

  it("lets a member cancel only the jobs they started", async () => {
    const requests = renderApp(backend("editor"), PATH);

    const mine = await screen.findByRole("listitem", { name: "Profile ERP" });
    const graces = screen.getByRole("listitem", { name: "Scan CRM" });
    expect(within(graces).queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();

    fireEvent.click(within(mine).getByRole("button", { name: "Cancel job" }));

    expect(await within(mine).findByText("Cancelled")).toBeInTheDocument();
    expect(
      requests.find((r) => r.method === "POST" && r.path === `/api/v1/jobs/${MINE.id}/cancel`),
    ).toMatchObject({ csrf: CSRF_TOKEN });
  });

  it("lets an owner cancel anyone's active job", async () => {
    renderApp(backend("owner"), PATH);

    const graces = await screen.findByRole("listitem", { name: "Scan CRM" });
    expect(within(graces).getByRole("button", { name: "Cancel job" })).toBeInTheDocument();
    const failed = screen.getByRole("listitem", { name: "Export model" });
    expect(within(failed).queryByRole("button", { name: "Cancel job" })).not.toBeInTheDocument();
  });
});
