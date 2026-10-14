import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type {
  LlmModel,
  LlmModelUsage,
  LlmProvider,
  LlmSetup,
} from "../api/llm";
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
const ADA: Me = {
  ...ROOT,
  email: "ada@example.com",
  display_name: "Ada",
  system_role: "user",
};

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

const UNUSED: LlmModelUsage = {
  in_use: false,
  roles: [],
  installation_roles: [],
  workspaces: [],
  indexed_workspaces: [],
};

const SPARE: LlmModel = {
  ...MODEL,
  id: "5d3a8b1e-0000-4000-8000-000000000003",
  name: "spare",
  test_ok: true,
};

const INCOMPLETE: LlmSetup = {
  complete: false,
  has_agent_model: true,
  has_tested_agent_model: false,
  has_internal_agent_model: false,
};

function as(
  me: Me,
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (request: ApiRequest) =>
    request.path === "/api/v1/me" ? json(me) : handle(request);
}

function providersApi(
  items: LlmProvider[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return as(ROOT, (r) => {
    const handled = handle(r);
    if (handled) return handled;
    if (r.method === "GET" && r.path === "/api/v1/admin/llm/providers")
      return json({ items });
    if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
    return undefined;
  });
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("AI setup banner", () => {
  it("warns admins until an agent model is registered and tested", async () => {
    renderApp(
      as(ROOT, (r) =>
        r.path === "/api/v1/admin/llm/setup" ? json(INCOMPLETE) : undefined,
      ),
      "/",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "AI setup is incomplete",
    );
  });

  it("is gone once setup is complete", async () => {
    renderApp(
      as(ROOT, (r) =>
        r.path === "/api/v1/admin/llm/setup"
          ? json({ ...INCOMPLETE, complete: true })
          : undefined,
      ),
      "/",
    );

    await screen.findByRole("navigation", { name: "Administration" });
    await waitFor(() =>
      expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
    );
  });
});

describe("language models page", () => {
  it("is for admins only", async () => {
    renderApp(as(ADA), "/admin/llm");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only an administrator can see this page.",
    );
  });

  it("lists every model in one table with its provider, never showing a key", async () => {
    const external: LlmProvider = {
      ...PROVIDER,
      id: "5d3a8b1e-0000-4000-8000-000000000009",
      name: "OpenAI",
      internal: false,
      models: [
        { ...MODEL, id: "5d3a8b1e-0000-4000-8000-000000000008", name: "gpt" },
      ],
    };
    renderApp(providersApi([PROVIDER, external]), "/admin/llm");

    const qwen = await screen.findByRole("row", { name: "Model qwen3" });
    expect(qwen).toHaveTextContent("Local vLLM");
    expect(qwen).toHaveTextContent("Internal");
    expect(screen.getByRole("row", { name: "Model gpt" })).toHaveTextContent(
      "External",
    );
    const local = screen.getByRole("row", { name: "Provider Local vLLM" });
    expect(local).toHaveTextContent("API key saved");
  });

  async function openAddProvider() {
    fireEvent.click(
      await screen.findByRole("button", { name: "Add provider" }),
    );
    return screen.findByRole("dialog", { name: "Add a provider" });
  }

  it("adds a provider with its key", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") {
          return r.method === "POST"
            ? json(PROVIDER, 201)
            : json({ items: [] });
        }
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    const dialog = await openAddProvider();
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Local vLLM" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Base URL/), {
      target: { value: "http://vllm:8000/v1" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^API key/), {
      target: { value: "sk-secret" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Add provider" }),
    );

    await waitFor(() =>
      expect(requests.some((r) => r.method === "POST")).toBe(true),
    );
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

  it("adds an Anthropic provider when that type is chosen", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/llm/providers") {
          return r.method === "POST"
            ? json(PROVIDER, 201)
            : json({ items: [] });
        }
        if (r.path === "/api/v1/admin/llm/setup") return json(INCOMPLETE);
        return undefined;
      }),
      "/admin/llm",
    );

    const dialog = await openAddProvider();
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Claude" },
    });
    fireEvent.change(within(dialog).getByLabelText("Type"), {
      target: { value: "anthropic" },
    });
    fireEvent.change(within(dialog).getByLabelText(/^Base URL/), {
      target: { value: "https://api.anthropic.com/v1" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Add provider" }),
    );

    await waitFor(() =>
      expect(requests.some((r) => r.method === "POST")).toBe(true),
    );
    const body = requests.find((r) => r.method === "POST")?.body as {
      adapter: string;
    };
    expect(body.adapter).toBe("anthropic");
  });

  it("edits a provider, keeping its saved key when the field is left empty", async () => {
    const requests = renderApp(
      providersApi([PROVIDER], (r) =>
        r.method === "PUT" &&
        r.path === `/api/v1/admin/llm/providers/${PROVIDER.id}`
          ? json(PROVIDER)
          : undefined,
      ),
      "/admin/llm",
    );

    const row = await screen.findByRole("row", { name: "Provider Local vLLM" });
    fireEvent.click(within(row).getByRole("button", { name: "Edit provider" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit provider" });
    fireEvent.change(within(dialog).getByLabelText("Name"), {
      target: { value: "Primary vLLM" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      name: "Primary vLLM",
      adapter: "openai_compatible",
      base_url: "http://vllm:8000/v1",
      internal: true,
      timeout_seconds: 60,
    });
  });

  it("adds a model to a chosen provider", async () => {
    const requests = renderApp(
      providersApi([PROVIDER], (r) =>
        r.method === "POST" &&
        r.path === `/api/v1/admin/llm/providers/${PROVIDER.id}/models`
          ? json(MODEL, 201)
          : undefined,
      ),
      "/admin/llm",
    );

    await screen.findByRole("row", { name: "Model qwen3" });
    fireEvent.click(screen.getByRole("button", { name: "Add model" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a model" });
    fireEvent.change(await within(dialog).findByLabelText("Model name"), {
      target: { value: "llama" },
    });
    expect(within(dialog).queryByLabelText("Role")).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Add model" }));

    await waitFor(() =>
      expect(requests.some((r) => r.method === "POST")).toBe(true),
    );
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      name: "llama",
    });
  });

  describe("picking a model from the provider's list", () => {
    const AVAILABLE = `/api/v1/admin/llm/providers/${PROVIDER.id}/available-models`;

    function openDialog(
      available: Response,
      requestsOut: { post?: ApiRequest } = {},
    ) {
      const requests = renderApp(
        providersApi([PROVIDER], (r) => {
          if (r.method === "GET" && r.path === AVAILABLE) return available;
          if (r.method === "POST" && r.path.endsWith("/models")) {
            requestsOut.post = r;
            return json(MODEL, 201);
          }
          return undefined;
        }),
        "/admin/llm",
      );
      return requests;
    }

    async function open() {
      await screen.findByRole("row", { name: "Model qwen3" });
      fireEvent.click(screen.getByRole("button", { name: "Add model" }));
      return screen.findByRole("dialog", { name: "Add a model" });
    }

    it("lists the provider's models, disables registered ones, searches and adds the pick", async () => {
      const out: { post?: ApiRequest } = {};
      openDialog(
        json({
          supported: true,
          models: [
            { name: "llama", registered: false },
            { name: "qwen3", registered: true },
            { name: "mistral", registered: false },
          ],
        }),
        out,
      );

      const dialog = await open();
      const registered = await within(dialog).findByRole("option", {
        name: /qwen3/,
      });
      expect(within(registered).getByRole("button")).toBeDisabled();
      expect(
        within(within(dialog).getByRole("listbox")).getAllByRole("option"),
      ).toHaveLength(3);

      fireEvent.change(within(dialog).getByLabelText("Model name"), {
        target: { value: "MIS" },
      });
      expect(
        within(within(dialog).getByRole("listbox")).getAllByRole("option"),
      ).toHaveLength(1);
      fireEvent.click(within(dialog).getByRole("button", { name: "mistral" }));
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Add model" }),
      );

      await waitFor(() => expect(out.post).toBeDefined());
      expect(out.post?.body).toEqual({ name: "mistral" });
    });

    it("asks for a choice before adding", async () => {
      const out: { post?: ApiRequest } = {};
      openDialog(
        json({
          supported: true,
          models: [{ name: "llama", registered: false }],
        }),
        out,
      );

      const dialog = await open();
      await within(dialog).findByRole("option", { name: /llama/ });
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Add model" }),
      );

      expect(
        await within(dialog).findByText("Choose or type a model name."),
      ).toBeInTheDocument();
      expect(out.post).toBeUndefined();
    });

    it("falls back to typing, with the error shown, when listing fails", async () => {
      const out: { post?: ApiRequest } = {};
      openDialog(
        json({
          supported: true,
          models: [],
          error: "The provider rejected the API key.",
          error_code: "auth",
        }),
        out,
      );

      const dialog = await open();
      expect(
        await within(dialog).findByText(/The provider rejected the API key\./),
      ).toBeInTheDocument();
      expect(within(dialog).queryByRole("listbox")).not.toBeInTheDocument();
      fireEvent.change(within(dialog).getByLabelText("Model name"), {
        target: { value: "typed-model" },
      });
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Add model" }),
      );

      await waitFor(() => expect(out.post).toBeDefined());
      expect(out.post?.body).toEqual({ name: "typed-model" });
    });

    it("goes straight to typing when the provider cannot list", async () => {
      openDialog(json({ supported: false, models: [] }));

      const dialog = await open();

      expect(
        await within(dialog).findByText(/cannot list its models/),
      ).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Model name")).toBeEnabled();
    });
  });

  it("edits only a model's context window", async () => {
    const requests = renderApp(
      providersApi([PROVIDER], (r) =>
        r.method === "PUT" && r.path === `/api/v1/admin/llm/models/${MODEL.id}`
          ? json(MODEL)
          : undefined,
      ),
      "/admin/llm",
    );

    const row = await screen.findByRole("row", { name: "Model qwen3" });
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit model" });
    expect(within(dialog).getByLabelText("Model name")).toHaveAttribute(
      "readonly",
    );
    expect(within(dialog).getByLabelText("Model name")).toHaveValue("qwen3");
    fireEvent.change(
      within(dialog).getByLabelText(
        "Context window in tokens (empty: ask the server)",
      ),
      { target: { value: "32768" } },
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      context_window: 32768,
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
          return json({
            items: [{ ...PROVIDER, models: [done ? tested : MODEL] }],
          });
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

    fireEvent.click(
      await screen.findByRole("button", { name: "Test connection" }),
    );

    expect(await screen.findByText(/Tool calling: yes/)).toHaveTextContent(
      "Tool calling: yes · streaming: yes · JSON schema: no · context window: 32768",
    );
    expect(
      requests.some((r) => r.method === "POST" && r.path.endsWith("/test")),
    ).toBe(true);
  });

  it("shows why a test failed", async () => {
    const failed: LlmModel = {
      ...MODEL,
      test_ok: false,
      test_error_code: "auth",
      test_error: "The provider rejected the API key.",
    };
    renderApp(providersApi([{ ...PROVIDER, models: [failed] }]), "/admin/llm");

    expect(await screen.findByText(/Test failed \(auth\)/)).toHaveTextContent(
      "The provider rejected the API key.",
    );
  });

  it("removes a model only after confirmation", async () => {
    const requests = renderApp(
      providersApi([PROVIDER], (r) =>
        r.path === `/api/v1/admin/llm/models/${MODEL.id}/usage`
          ? json(UNUSED)
          : r.path === `/api/v1/admin/llm/models/${MODEL.id}`
            ? noContent()
            : undefined,
      ),
      "/admin/llm",
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove model" }),
    );
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    const dialog = await screen.findByRole("dialog", { name: "Remove qwen3?" });
    const confirm = within(dialog).getByRole("button", {
      name: "Remove model",
    });
    await waitFor(() => expect(confirm).toBeEnabled());
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(requests.some((r) => r.method === "DELETE")).toBe(true),
    );
  });

  describe("removing a model in use", () => {
    const REMOVAL = `/api/v1/admin/llm/models/${MODEL.id}/removal`;
    const inUse = (extra: Partial<LlmModelUsage> = {}): LlmModelUsage => ({
      in_use: true,
      roles: ["agent"],
      installation_roles: ["agent"],
      workspaces: [],
      indexed_workspaces: [],
      ...extra,
    });
    const open = async (
      usage: LlmModelUsage,
      removal?: (r: ApiRequest) => Response,
    ) => {
      const requests = renderApp(
        providersApi([{ ...PROVIDER, models: [MODEL, SPARE] }], (r) => {
          if (r.path === `/api/v1/admin/llm/models/${MODEL.id}/usage`)
            return json(usage);
          return r.path === REMOVAL && removal ? removal(r) : undefined;
        }),
        "/admin/llm",
      );
      fireEvent.click(
        (await screen.findAllByRole("button", { name: "Remove model" }))[0]!,
      );
      return {
        requests,
        dialog: await screen.findByRole("dialog", { name: "Remove qwen3?" }),
      };
    };

    it("asks for a replacement per role and sends it", async () => {
      const { requests, dialog } = await open(inUse(), () => noContent());
      const confirm = await within(dialog).findByRole("button", {
        name: "Remove model",
      });
      await within(dialog).findByLabelText("Replace as Agent model");
      expect(confirm).toBeDisabled();

      fireEvent.change(
        within(dialog).getByLabelText("Replace as Agent model"),
        {
          target: { value: SPARE.id },
        },
      );
      fireEvent.click(confirm);

      await waitFor(() =>
        expect(requests.some((r) => r.path === REMOVAL)).toBe(true),
      );
      expect(requests.find((r) => r.path === REMOVAL)?.body).toMatchObject({
        agent_model_id: SPARE.id,
        workspace_replacements: [],
      });
      expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    });

    it("asks for another model for the Workspaces the replacement is not allowed in", async () => {
      const ws = "7c1d0000-0000-4000-8000-000000000001";
      let first = true;
      const { requests, dialog } = await open(inUse(), () => {
        if (!first) return noContent();
        first = false;
        return json(
          {
            error: {
              code: "replacement_not_allowed",
              message: "not allowed",
              details: {
                workspaces: [{ id: ws, name: "Narrow", roles: ["agent"] }],
              },
            },
          },
          409,
        );
      });
      fireEvent.change(
        await within(dialog).findByLabelText("Replace as Agent model"),
        {
          target: { value: SPARE.id },
        },
      );
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Remove model" }),
      );

      const own = await within(dialog).findByLabelText("Narrow: Agent model");
      expect(
        within(dialog).getByRole("button", { name: "Remove model" }),
      ).toBeDisabled();
      fireEvent.change(own, { target: { value: SPARE.id } });
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Remove model" }),
      );

      await waitFor(() =>
        expect(requests.filter((r) => r.path === REMOVAL)).toHaveLength(2),
      );
      expect(requests.filter((r) => r.path === REMOVAL)[1]?.body).toMatchObject(
        {
          workspace_replacements: [
            { workspace_id: ws, agent_model_id: SPARE.id },
          ],
        },
      );
    });

    it("refuses an embedding model whose documents are indexed, naming the Workspaces", async () => {
      const { requests, dialog } = await open(
        inUse({
          roles: ["embedding"],
          indexed_workspaces: [
            {
              id: "7c1d0000-0000-4000-8000-000000000001",
              name: "Finance",
              roles: [],
            },
          ],
        }),
      );

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Finance",
      );
      expect(
        within(dialog).getByRole("button", { name: "Remove model" }),
      ).toBeDisabled();
      expect(requests.some((r) => r.path === REMOVAL)).toBe(false);
    });
  });

  it("removes a provider only after confirmation", async () => {
    const requests = renderApp(
      providersApi([PROVIDER], (r) =>
        r.path === `/api/v1/admin/llm/providers/${PROVIDER.id}` &&
        r.method === "DELETE"
          ? noContent()
          : undefined,
      ),
      "/admin/llm",
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove provider" }),
    );
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    const dialog = await screen.findByRole("dialog", {
      name: "Remove Local vLLM?",
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Remove provider" }),
    );

    await waitFor(() =>
      expect(requests.some((r) => r.method === "DELETE")).toBe(true),
    );
  });
});
