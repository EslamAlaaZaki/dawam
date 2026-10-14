import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LlmModel, LlmProvider } from "../api/llm";
import type { LlmBudgets, LlmUsage } from "../api/llmSettings";
import type { Me } from "../api/queries";
import {
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

const HINT = "Dedicated embedding models usually give better document search";

const BIG = model("5d3a8b1e-0000-4000-8000-0000000000a1", "big");
const UNTESTED = model("5d3a8b1e-0000-4000-8000-0000000000a2", "fresh", {
  test_ok: null,
});
const EMBED = model("5d3a8b1e-0000-4000-8000-0000000000a3", "embed", {
  roles: ["embedding"],
});

const OTHER = model("5d3a8b1e-0000-4000-8000-0000000000a4", "other");
const CLOUD = model("5d3a8b1e-0000-4000-8000-0000000000b1", "cloud");

const EXTERNAL: LlmProvider = {
  id: "5d3a8b1e-0000-4000-8000-000000000009",
  name: "Cloud",
  adapter: "openai_compatible",
  base_url: "https://api.example.com/v1",
  has_api_key: true,
  internal: false,
  timeout_seconds: 60,
  models: [CLOUD],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const PROVIDER: LlmProvider = {
  id: "5d3a8b1e-0000-4000-8000-000000000001",
  name: "Local",
  adapter: "openai_compatible",
  base_url: "http://vllm:8000/v1",
  has_api_key: false,
  internal: true,
  timeout_seconds: 60,
  models: [BIG, UNTESTED, EMBED, OTHER],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
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

function api(extra?: (r: ApiRequest) => Response | undefined) {
  return (r: ApiRequest) => {
    if (r.path === "/api/v1/me") return json(ROOT);
    if (r.path === "/api/v1/admin/llm/providers")
      return json({ items: [PROVIDER, EXTERNAL] });
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
  it("has no installation default model per role", async () => {
    renderApp(api(), "/admin/llm/settings");

    await screen.findByRole("row", { name: "Roles of big" });
    expect(screen.queryByLabelText(/^Agent model/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Embedding model")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save roles" }),
    ).not.toBeInTheDocument();
  });

  it("lists every model with provider, internal flag, last test and role checkboxes", async () => {
    renderApp(api(), "/admin/llm/settings");

    const big = await screen.findByRole("row", { name: "Roles of big" });
    expect(big).toHaveTextContent("Local");
    expect(big).toHaveTextContent("Internal");
    expect(big).toHaveTextContent("Passed");
    expect(within(big).getByLabelText("Agent role of big")).toBeChecked();
    expect(within(big).getByLabelText("Light role of big")).not.toBeChecked();
    expect(screen.getByRole("row", { name: "Roles of fresh" })).toHaveTextContent(
      "Not tested",
    );
  });

  it("saves any combination of roles for a model, and hints about embedding", async () => {
    const saved: LlmModel = { ...BIG, roles: ["agent", "light", "embedding"] };
    const requests = renderApp(
      api((r) =>
        r.method === "PUT" && r.path === `/api/v1/admin/llm/models/${BIG.id}/roles`
          ? json(saved)
          : undefined,
      ),
      "/admin/llm/settings",
    );

    const row = await screen.findByRole("row", { name: "Roles of big" });
    expect(screen.queryByText(HINT)).not.toBeInTheDocument();
    fireEvent.click(within(row).getByLabelText("Light role of big"));
    fireEvent.click(within(row).getByLabelText("Embedding role of big"));
    expect(screen.getByText(HINT)).toBeInTheDocument();
    fireEvent.click(
      within(row).getByRole("button", { name: "Save roles of big" }),
    );

    await waitFor(() =>
      expect(requests.some((r) => r.path.endsWith("/roles") && r.method === "PUT" && r.path.includes("models"))).toBe(true),
    );
    expect(requests.find((r) => r.path.includes("/models/"))?.body).toEqual({
      roles: ["agent", "light", "embedding"],
    });
  });

  it("shows the refusal when a role cannot be saved", async () => {
    renderApp(
      api((r) =>
        r.method === "PUT" && r.path.includes("/models/")
          ? json(
              {
                error: {
                  code: "embedding_test_failed",
                  message: "The embedding test failed: no embeddings here.",
                },
              },
              422,
            )
          : undefined,
      ),
      "/admin/llm/settings",
    );

    const row = await screen.findByRole("row", { name: "Roles of big" });
    fireEvent.click(within(row).getByLabelText("Embedding role of big"));
    fireEvent.click(
      within(row).getByRole("button", { name: "Save roles of big" }),
    );

    expect(await within(row).findByText(/no embeddings here/)).toBeInTheDocument();
  });

  describe("unticking a role a Workspace depends on", () => {
    const ROLES = `/api/v1/admin/llm/models/${BIG.id}/roles`;
    const conflict = (internalOnly: boolean) =>
      json(
        {
          error: {
            code: "replacement_required",
            message: "Choose a replacement.",
            details: {
              workspaces: [
                {
                  id: WORKSPACE,
                  name: "Core",
                  roles: ["agent"],
                  internal_only: internalOnly,
                },
              ],
            },
          },
        },
        409,
      );
    const untick = async (internalOnly: boolean) => {
      let first = true;
      const requests = renderApp(
        api((r) => {
          if (r.method !== "PUT" || r.path !== ROLES) return undefined;
          if (!first) return json({ ...BIG, roles: [] });
          first = false;
          return conflict(internalOnly);
        }),
        "/admin/llm/settings",
      );
      const row = await screen.findByRole("row", { name: "Roles of big" });
      fireEvent.click(within(row).getByLabelText("Agent role of big"));
      fireEvent.click(
        within(row).getByRole("button", { name: "Save roles of big" }),
      );
      return {
        requests,
        dialog: await screen.findByRole("dialog", { name: "Replace big?" }),
      };
    };
    const optionNames = (select: HTMLElement) =>
      within(select)
        .getAllByRole("option")
        .map((o) => o.textContent);

    it("asks for a replacement per Workspace and role, then saves with it", async () => {
      const { requests, dialog } = await untick(false);
      const pick = within(dialog).getByLabelText("Core: Agent model");
      const confirm = within(dialog).getByRole("button", {
        name: "Replace and save roles",
      });

      expect(optionNames(pick)).toEqual(["Choose a model", "other", "cloud"]);
      expect(confirm).toBeDisabled();
      fireEvent.change(pick, { target: { value: OTHER.id } });
      fireEvent.click(confirm);

      await waitFor(() =>
        expect(requests.filter((r) => r.path === ROLES)).toHaveLength(2),
      );
      expect(requests.filter((r) => r.path === ROLES)[1]?.body).toEqual({
        roles: [],
        workspace_replacements: [
          { workspace_id: WORKSPACE, agent_model_id: OTHER.id },
        ],
      });
      await waitFor(() =>
        expect(
          screen.queryByRole("dialog", { name: "Replace big?" }),
        ).not.toBeInTheDocument(),
      );
    });

    it("offers an internal-only Workspace only internal models", async () => {
      const { dialog } = await untick(true);

      expect(
        optionNames(within(dialog).getByLabelText("Core: Agent model")),
      ).toEqual(["Choose a model", "other"]);
      expect(dialog).toHaveTextContent("internal models only");
    });
  });

  it("sets the installation budget and a Workspace budget", async () => {
    const requests = renderApp(
      api((r) => (r.method === "PUT" ? noContent() : undefined)),
      "/admin/llm/settings",
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Budget settings" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "Budget settings" });
    const installation = await within(dialog).findByLabelText(
      /^Installation \(tokens/,
    );
    fireEvent.change(installation, { target: { value: "2000" } });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save installation budget" }),
    );
    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      monthly_token_budget: 2000,
    });

    fireEvent.change(await within(dialog).findByLabelText("Workspace"), {
      target: { value: WORKSPACE },
    });
    fireEvent.change(within(dialog).getByLabelText("Tokens per month"), {
      target: { value: "300" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Set Workspace budget" }),
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
