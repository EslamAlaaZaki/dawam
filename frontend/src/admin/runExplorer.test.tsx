import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { RunDetail, RunSummary } from "../api/adminRuns";
import type { Me } from "../api/queries";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ADMIN: Me = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};
const USER: Me = { ...ADMIN, email: "ada@example.com", system_role: "user" };

const RUN: RunSummary = {
  id: "aaaaaaaa-0000-4000-8000-000000000001",
  workspace_id: "bbbbbbbb-0000-4000-8000-000000000001",
  workspace_name: "Retail DW",
  conversation_id: "cccccccc-0000-4000-8000-000000000001",
  member_id: "dddddddd-0000-4000-8000-000000000001",
  member_email: "grace@example.com",
  status: "completed",
  prompt_version: "pv-123",
  model_name: "big-model",
  input_tokens: 30,
  output_tokens: 10,
  duration_ms: 420,
  cost: { amount: "0.00024", currency: "USD" },
  tool_call_count: 1,
  thumbs_up: 0,
  thumbs_down: 1,
  started_at: "2026-10-05T09:00:00Z",
};

const DETAIL: RunDetail = {
  run: RUN,
  finished_at: "2026-10-05T09:00:01Z",
  timeline: [
    {
      kind: "model",
      name: "big-model",
      status: "completed",
      duration_ms: 420,
      input_tokens: 30,
      output_tokens: 10,
      cost: RUN.cost,
      prompt: "Mail the result to [EMAIL] please",
      output: "It is Net Revenue.",
      arguments: null,
      result: null,
      error_code: null,
      error_message: null,
    },
    {
      kind: "tool",
      name: "list_kpis",
      status: "ok",
      duration_ms: 12,
      input_tokens: null,
      output_tokens: null,
      cost: null,
      prompt: null,
      output: null,
      arguments: { limit: 5 },
      result: null,
      error_code: null,
      error_message: null,
    },
  ],
  feedback: [
    {
      member_email: "grace@example.com",
      rating: "down",
      comment: "Missed the filter",
      updated_at: "2026-10-05T10:00:00Z",
    },
  ],
};

function backend(me: Me, seen: ApiRequest[] = []) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(me);
      case "GET /api/v1/admin/assistant/runs":
        seen.push(r);
        return json({ items: [RUN] });
      case `GET /api/v1/admin/assistant/runs/${RUN.id}`:
        return json(DETAIL);
      case "GET /api/v1/admin/workspaces":
      case "GET /api/v1/admin/users":
        return json({ items: [], next_cursor: null });
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

describe("the assistant run explorer", () => {
  it("is linked from the admin navigation and lists runs", async () => {
    renderApp(backend(ADMIN), "/admin/ai-runs");

    const nav = await screen.findByRole("navigation", { name: "Administration" });
    expect(within(nav).getByRole("link", { name: "Assistant runs" })).toHaveAttribute(
      "href",
      "/admin/ai-runs",
    );
    const row = await screen.findByRole("row", { name: `Run ${RUN.id}` });
    expect(row).toHaveTextContent("Retail DW");
    expect(row).toHaveTextContent("grace@example.com");
    expect(row).toHaveTextContent("30 / 10");
    expect(row).toHaveTextContent("0.000240 USD");
    expect(row).toHaveTextContent("0 up, 1 down");
  });

  it("shows a run's timeline, prompt version and feedback", async () => {
    renderApp(backend(ADMIN), "/admin/ai-runs");

    fireEvent.click(await screen.findByRole("button", { name: `Open run ${RUN.id}` }));

    const timeline = await screen.findByRole("region", { name: "Run timeline" });
    expect(timeline).toHaveTextContent("pv-123");
    const steps = within(timeline).getAllByRole("listitem", { name: /Model calls|Tool call/ });
    expect(steps.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Model calls big-model",
      "Tool call list_kpis",
    ]);
    expect(steps[0]).toHaveTextContent("Mail the result to [EMAIL] please");
    expect(steps[0]).toHaveTextContent("30 in / 10 out tokens");
    expect(steps[1]).toHaveTextContent("12 ms");
    expect(timeline).toHaveTextContent("grace@example.com: thumbs down — Missed the filter");
  });

  it("sends the chosen filters to the API", async () => {
    const seen: ApiRequest[] = [];
    renderApp(backend(ADMIN, seen), "/admin/ai-runs");
    await screen.findByRole("row", { name: `Run ${RUN.id}` });

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));

    await screen.findByRole("row", { name: `Run ${RUN.id}` });
    expect(seen.some((r) => r.query.status === "failed")).toBe(true);
  });

  it("tells other users the page is for administrators", async () => {
    renderApp(backend(USER), "/admin/ai-runs");

    expect(await screen.findByText("Only an administrator can see this page.")).toBeInTheDocument();
  });
});
