import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LlmModel, LlmProvider, LlmSetup } from "../api/llm";
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
const ADA: Me = { ...ROOT, email: "ada@example.com", display_name: "Ada", system_role: "user" };

const MODEL: LlmModel = {
  id: "5d3a8b1e-0000-4000-8000-000000000002",
  provider_id: "5d3a8b1e-0000-4000-8000-000000000001",
  name: "qwen3",
  roles: ["agent"],
  context_window: null,
  tool_calling: null,
  streaming: null,
  json_schema: null,
  embedding_dimension: null,
  limited: false,
  test_ok: null,
  test_error_code: null,
  test_error: null,
  last_tested_at: null,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const PROVIDER: LlmProvider = {
  id: "5d3a8b1e-0000-4000-8000-000000000001",
  name: "Local vLLM",
  adapter: "openai_compatible",
  base_url: "http://vllm:8000/v1",
  has_api_key: true,
  internal: true,
  timeout_seconds: 60,
  models: [MODEL],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const INCOMPLETE: LlmSetup = {
  complete: false,
  has_agent_model: true,
  has_tested_agent_model: false,
  has_internal_agent_model: false,
};

function as(me: Me, handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  return (request: ApiRequest) => (request.path === "/api/v1/me" ? json(me) : handle(request));
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("AI setup banner", () => {
  it("warns admins until an agent model is registered and tested", async () => {
    renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/llm/setup" ? json(INCOMPLETE) : undefined)),
      "/",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("AI setup is incomplete");
  });

  it("is gone once setup is complete", async () => {
    renderApp(
      as(ROOT, (r) =>
        r.path === "/api/v1/admin/llm/setup" ? json({ ...INCOMPLETE, complete: true }) : undefined,
      ),
      "/",
    );

    await screen.findByRole("navigation", { name: "Administration" });
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});

describe("language models page", () => {
  it("is for admins only", async () => {
    renderApp(as(ADA), "/admin/llm");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only an administrator can see this page.",
    );
  });

  it("lists providers flagged internal or external, never showing a key", async () => {
    const external: LlmProvider = {
      ...PROVIDER,
      id: "5d3a8b1e-0000-4000-8000-000000000009",
      name: "OpenAI",
      internal: false,
      models: [],
    };
    renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") return json({ items: [PROVIDER, external] });
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    const local = await screen.findByRole("article", { name: "Provider Local vLLM" });
    expect(within(local).getByText("(internal)")).toBeInTheDocument();
    expect(within(local).getByText(/API key saved/)).toBeInTheDocument();
    const remote = screen.getByRole("article", { name: "Provider OpenAI" });
    expect(within(remote).getByText("(external)")).toBeInTheDocument();
  });

  it("adds a provider with its key", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") {
          return r.method === "POST" ? json(PROVIDER, 201) : json({ items: [] });
        }
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Local vLLM" } });
    fireEvent.change(screen.getByLabelText(/^Base URL/), {
      target: { value: "http://vllm:8000/v1" },
    });
    fireEvent.change(screen.getByLabelText(/^API key/), { target: { value: "sk-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));

    await waitFor(() => expect(requests.some((r) => r.method === "POST")).toBe(true));
    expect(requests.find((r) => r.method === "POST")).toEqual({
      method: "POST",
      path: "/api/v1/admin/llm/providers",
      query: {},
      csrf: CSRF_TOKEN,
      body: {
        name: "Local vLLM",
        adapter: "openai_compatible",
        base_url: "http://vllm:8000/v1",
        api_key: "sk-secret",
        internal: true,
        timeout_seconds: 60,
      },
    });
  });

  it("tests a model and shows what it found", async () => {
    const tested: LlmModel = {
      ...MODEL,
      test_ok: true,
      tool_calling: true,
      streaming: true,
      json_schema: false,
      context_window: 32768,
    };
    let done = false;
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") {
          return json({ items: [{ ...PROVIDER, models: [done ? tested : MODEL] }] });
        }
        if (r.path === `/api/v1/admin/llm/models/${MODEL.id}/test`) {
          done = true;
          return json(tested);
        }
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/Tool calling: yes/)).toHaveTextContent(
      "Tool calling: yes · streaming: yes · JSON schema: no · context window: 32768",
    );
    expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/test"))).toBe(true);
  });

  it("shows why a test failed", async () => {
    const failed: LlmModel = {
      ...MODEL,
      test_ok: false,
      test_error_code: "auth",
      test_error: "The provider rejected the API key.",
    };
    renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") {
          return json({ items: [{ ...PROVIDER, models: [failed] }] });
        }
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    expect(await screen.findByText(/Test failed \(auth\)/)).toHaveTextContent(
      "The provider rejected the API key.",
    );
  });

  it("removes a model", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") return json({ items: [PROVIDER] });
        if (r.path === `/api/v1/admin/llm/models/${MODEL.id}`) return noContent();
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove model" }));

    await waitFor(() => expect(requests.some((r) => r.method === "DELETE")).toBe(true));
  });
});
