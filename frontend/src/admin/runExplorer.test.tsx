import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AiJob, AiJobDetail } from "../api/adminAiJobs";
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
    expect(row).toHaveTextContent("420 ms");
    expect(row).toHaveTextContent("30 / 10");
    expect(row).toHaveTextContent("0.000240 USD");
    expect(row).toHaveTextContent("big-model");
    expect(row).toHaveTextContent("0 up 1 down");
    expect(screen.getByRole("heading", { name: "AI runs" })).toBeInTheDocument();
  });

  it("shows a run's timeline, prompt version and feedback", async () => {
    renderApp(backend(ADMIN), "/admin/ai-runs");

    fireEvent.click(await screen.findByRole("row", { name: `Run ${RUN.id}` }));

    const timeline = await screen.findByRole("region", { name: "Run timeline" });
    expect(screen.getByRole("dialog")).toHaveTextContent("pv-123");
    const steps = within(timeline).getAllByRole("listitem", { name: /Model calls|Tool call/ });
    expect(steps.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Model calls big-model",
      "Tool call list_kpis",
    ]);
    expect(steps[0]).toHaveTextContent("Mail the result to [EMAIL] please");
    expect(steps[0]).toHaveTextContent("30 in / 10 out tokens");
    expect(steps[1]).toHaveTextContent("12 ms");
    const feedback = screen.getByRole("region", { name: "Member feedback" });
    expect(feedback).toHaveTextContent("Thumbs down");
    expect(feedback).toHaveTextContent("grace@example.com");
    expect(feedback).toHaveTextContent("Missed the filter");
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

const JOB: AiJob = {
  key: "dddddddd-0000-4000-8000-0000000000aa",
  feature: "pii_scan",
  target: "Scan Retail DW",
  job_id: "dddddddd-0000-4000-8000-0000000000aa",
  workspace_id: "bbbbbbbb-0000-4000-8000-000000000001",
  workspace_name: "Retail DW",
  started_by: "grace@example.com",
  member_count: null,
  started_at: "2026-10-05T09:00:00Z",
  status: "partial",
  calls: 4,
  failed_calls: 1,
  duration_ms: 8400,
  input_tokens: 1200,
  output_tokens: 300,
  cost: null,
  models: ["small-model", "big-model"],
};
const SEARCH: AiJob = {
  ...JOB,
  key: "search:bbbbbbbb-0000-4000-8000-000000000001:2026-10-05",
  feature: "search",
  target: null,
  job_id: null,
  started_by: "3 members",
  member_count: 3,
  status: "completed",
  failed_calls: 0,
  cost: { amount: "0.0012", currency: "USD" },
};
const JOB_DETAIL: AiJobDetail = {
  job: JOB,
  models: [
    {
      role: "classifier",
      model_name: "small-model",
      calls: 3,
      failed_calls: 0,
      avg_latency_ms: 900,
      input_tokens: 900,
      output_tokens: 100,
      cost: { amount: "0.0005", currency: "USD" },
    },
    {
      role: "reasoner",
      model_name: "big-model",
      calls: 1,
      failed_calls: 1,
      avg_latency_ms: null,
      input_tokens: 300,
      output_tokens: 200,
      cost: null,
    },
  ],
  failed_calls: [
    { at: "2026-10-05T09:00:05Z", error_code: "rate_limited", role: "reasoner", model_name: "big-model" },
  ],
};

function jobsBackend(seen: ApiRequest[] = []) {
  const base = backend(ADMIN);
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/admin/ai/jobs":
        seen.push(r);
        return json({ items: [JOB, SEARCH] });
      case `GET /api/v1/admin/ai/jobs/${JOB.key}`:
        return json(JOB_DETAIL);
      default:
        return base(r);
    }
  };
}

describe("the Jobs & search tab", () => {
  it("lists jobs and search rows with a summary", async () => {
    renderApp(jobsBackend(), "/admin/ai-runs");
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Jobs & search" }), { button: 0 });
    fireEvent.click(screen.getByRole("tab", { name: "Jobs & search" }));

    const job = await screen.findByRole("row", { name: `Job ${JOB.key}` });
    expect(job).toHaveTextContent("PII scan");
    expect(job).toHaveTextContent("Scan Retail DW");
    expect(job).toHaveTextContent("grace@example.com");
    expect(job).toHaveTextContent("1 failed");
    expect(job).toHaveTextContent("8.4 s");
    expect(job).toHaveTextContent("1.2k / 300");
    expect(job).toHaveTextContent("unpriced");
    expect(job).toHaveTextContent("small-model");
    const search = screen.getByRole("row", { name: `Job ${SEARCH.key}` });
    expect(search).toHaveTextContent("Document search");
    expect(search).toHaveTextContent("3 members");
    expect(search).toHaveTextContent("0.001200 USD");
  });

  it("sends the chosen filters to the API", async () => {
    const seen: ApiRequest[] = [];
    renderApp(jobsBackend(seen), "/admin/ai-runs");
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Jobs & search" }), { button: 0 });
    await screen.findByRole("row", { name: `Job ${JOB.key}` });

    const form = screen.getByRole("form", { name: "Filter jobs" });
    fireEvent.change(within(form).getByLabelText("Feature"), { target: { value: "search" } });
    fireEvent.change(within(form).getByLabelText("Status"), { target: { value: "partial" } });
    fireEvent.click(within(form).getByRole("button", { name: "Filter" }));

    await screen.findByRole("row", { name: `Job ${JOB.key}` });
    expect(seen.some((r) => r.query.feature === "search" && r.query.status === "partial")).toBe(
      true,
    );
  });

  it("shows a job's calls by model, an unpriced cost and its errors", async () => {
    renderApp(jobsBackend(), "/admin/ai-runs");
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Jobs & search" }), { button: 0 });

    fireEvent.click(await screen.findByRole("row", { name: `Job ${JOB.key}` }));

    const calls = await screen.findByRole("region", { name: "Calls by model" });
    expect(within(calls).getByText("classifier")).toBeInTheDocument();
    expect(calls).toHaveTextContent("small-model");
    expect(calls).toHaveTextContent("0.000500 USD");
    expect(calls).toHaveTextContent("unpriced");
    expect(screen.getByRole("region", { name: "Token share" })).toHaveTextContent("67%");
    const errors = screen.getByRole("region", { name: "Errors" });
    expect(errors).toHaveTextContent("rate_limited");
    expect(errors).toHaveTextContent("big-model");
  });
});
