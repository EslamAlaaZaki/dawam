import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LlmModel, LlmProvider } from "../api/llm";
import type { LlmBudgets, LlmRoles, LlmUsage } from "../api/llmSettings";
import type { Me } from "../api/queries";
import {
  CSRF_TOKEN,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ROOT: Me = {
  id: "0b6c1f0e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};

const WORKSPACE = "7a1c0000-0000-4000-8000-000000000001";

function model(
  id: string,
  name: string,
  over: Partial<LlmModel> = {},
): LlmModel {
  return {
    id,
    provider_id: "5d3a8b1e-0000-4000-8000-000000000001",
    name,
    roles: ["agent"],
    context_window: null,
    tool_calling: true,
    streaming: true,
    json_schema: true,
    embedding_dimension: null,
    limited: false,
    test_ok: true,
    test_error_code: null,
    test_error: null,
    last_tested_at: null,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    ...over,
  };
}

const BIG = model("5d3a8b1e-0000-4000-8000-0000000000a1", "big");
const UNTESTED = model("5d3a8b1e-0000-4000-8000-0000000000a2", "fresh", {
  test_ok: null,
});
const EMBED = model("5d3a8b1e-0000-4000-8000-0000000000a3", "embed", {
  roles: ["embedding"],
});

const PROVIDER: LlmProvider = {
  id: "5d3a8b1e-0000-4000-8000-000000000001",
  name: "Local",
  adapter: "openai_compatible",
  base_url: "http://vllm:8000/v1",
  has_api_key: false,
  internal: true,
  timeout_seconds: 60,
  models: [BIG, UNTESTED, EMBED],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const ROLES: LlmRoles = {
  agent_model_id: BIG.id,
  light_model_id: null,
  embedding_model_id: null,
  reindex_needed: false,
  reindex_reason: null,
  reindex_flagged_at: null,
};

const BUDGETS: LlmBudgets = {
  installation_monthly_token_budget: 1000,
  workspaces: [
    {
      workspace_id: WORKSPACE,
      workspace_name: "Core",
      monthly_token_budget: 500,
    },
  ],
};

const TOTALS = {
  prompt_tokens: 80,
  completion_tokens: 20,
  total_tokens: 100,
  calls: 2,
  estimated_calls: 0,
};

const USAGE: LlmUsage = {
  month: "2026-01",
  totals: TOTALS,
  installation_monthly_token_budget: 1000,
  workspaces: [
    {
      workspace_id: WORKSPACE,
      workspace_name: "Core",
      totals: TOTALS,
      tokens_by_role: { agent: 100 },
      monthly_token_budget: 500,
    },
  ],
  users: [
    {
      user_id: ROOT.id,
      email: ROOT.email,
      display_name: "Root Admin",
      totals: TOTALS,
      tokens_by_role: { agent: 100 },
    },
  ],
};

function api(
  over: { roles?: LlmRoles } = {},
  extra?: (r: ApiRequest) => Response | undefined,
) {
  return (r: ApiRequest) => {
    if (r.path === "/api/v1/me") return json(ROOT);
    if (r.path === "/api/v1/admin/llm/providers")
      return json({ items: [PROVIDER] });
    if (r.path === "/api/v1/admin/llm/roles" && r.method === "GET") {
      return json(over.roles ?? ROLES);
    }
    if (r.path === "/api/v1/admin/llm/budgets") return json(BUDGETS);
    if (r.path === "/api/v1/admin/llm/usage") return json(USAGE);
    if (r.path === "/api/v1/admin/workspaces") {
      return json({
        items: [{ id: WORKSPACE, name: "Core" }],
        next_cursor: null,
      });
    }
    return extra?.(r);
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("AI roles, budgets and usage page", () => {
  it("offers only tested models for each role", async () => {
    renderApp(api(), "/admin/llm/settings");

    const agent = await screen.findByLabelText(/^Agent model/);
    const names = within(agent as HTMLElement)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(names).toContain("Local / big");
    expect(names).not.toContain("Local / fresh");
    expect(names).not.toContain("Local / embed");
    const embedding = screen.getByLabelText("Embedding model");
    expect(
      within(embedding).getByRole("option", { name: "Local / embed" }),
    ).toBeInTheDocument();
  });

  it("saves the roles", async () => {
    const requests = renderApp(
      api({}, (r) =>
        r.path === "/api/v1/admin/llm/roles" ? json(ROLES) : undefined,
      ),
      "/admin/llm/settings",
    );

    fireEvent.change(await screen.findByLabelText("Embedding model"), {
      target: { value: EMBED.id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save roles" }));

    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")).toEqual({
      method: "PUT",
      path: "/api/v1/admin/llm/roles",
      query: {},
      csrf: CSRF_TOKEN,
      body: {
        agent_model_id: BIG.id,
        light_model_id: null,
        embedding_model_id: EMBED.id,
      },
    });
  });

  it("says when documents must be indexed again", async () => {
    renderApp(
      api({
        roles: {
          ...ROLES,
          reindex_needed: true,
          reindex_reason: "embedding_model_changed",
        },
      }),
      "/admin/llm/settings",
    );

    expect(
      await screen.findByText(/must be indexed again/),
    ).toBeInTheDocument();
  });

  it("sets the installation budget and a Workspace budget", async () => {
    const requests = renderApp(
      api({}, (r) => (r.method === "PUT" ? noContent() : undefined)),
      "/admin/llm/settings",
    );

    const installation = await screen.findByLabelText(/^Installation \(tokens/);
    fireEvent.change(installation, { target: { value: "2000" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save installation budget" }),
    );
    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      monthly_token_budget: 2000,
    });

    fireEvent.change(await screen.findByLabelText("Workspace"), {
      target: { value: WORKSPACE },
    });
    fireEvent.change(screen.getByLabelText("Tokens per month"), {
      target: { value: "300" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Set Workspace budget" }),
    );
    await waitFor(() =>
      expect(
        requests.some((r) =>
          r.path.endsWith(`/budgets/workspaces/${WORKSPACE}`),
        ),
      ).toBe(true),
    );
  });

  it("shows usage per Workspace and per user and filters by month", async () => {
    const requests = renderApp(api(), "/admin/llm/settings");

    const perWorkspace = await screen.findByRole("table", {
      name: "Usage per Workspace",
    });
    expect(within(perWorkspace).getByText("Core")).toBeInTheDocument();
    const perUser = screen.getByRole("table", { name: "Usage per user" });
    expect(within(perUser).getByText("Root Admin")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Month"), {
      target: { value: "2025-12" },
    });
    await waitFor(() =>
      expect(requests.some((r) => r.query.month === "2025-12")).toBe(true),
    );
  });
});
