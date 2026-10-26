import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { LlmModel, LlmProvider } from "../api/llm";
import type { Me } from "../api/queries";
import {
  json,
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

function model(id: string, name: string, test_ok: boolean | null): LlmModel {
  return {
    id,
    provider_id: "p1",
    name,
    roles: ["agent"],
    context_window: null,
    tool_calling: null,
    streaming: null,
    json_schema: null,
    embedding_dimension: null,
    limited: false,
    test_ok,
    test_error_code: null,
    test_error: null,
    last_tested_at: null,
    warnings: [],
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const PROVIDER: LlmProvider = {
  id: "p1",
  name: "Local vLLM",
  adapter: "openai_compatible",
  base_url: "http://vllm:8000/v1",
  has_api_key: false,
  internal: true,
  timeout_seconds: 60,
  models: [
    model("m1", "primary", true),
    model("m2", "backup-a", true),
    model("m3", "backup-b", true),
    model("m4", "untested", null),
  ],
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

function api(state: { agent: string[] }, puts: unknown[]) {
  const list = () =>
    json({
      roles: [
        { role: "agent", model_ids: state.agent },
        { role: "light", model_ids: [] },
      ],
    });
  return (r: ApiRequest) => {
    if (r.path === "/api/v1/me") return json(ROOT);
    if (r.path === "/api/v1/admin/llm/providers")
      return json({ items: [PROVIDER] });
    if (r.path === "/api/v1/admin/llm/setup")
      return json({
        complete: true,
        has_agent_model: true,
        has_tested_agent_model: true,
        has_internal_agent_model: true,
      });
    if (r.path === "/api/v1/admin/llm/fallbacks") return list();
    if (r.path === "/api/v1/admin/llm/fallbacks/agent" && r.method === "PUT") {
      const body = r.body as { model_ids: string[] };
      puts.push(body);
      state.agent = body.model_ids;
      return list();
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("fallback models", () => {
  it("lists the ordered fallbacks in mono names and offers only tested models", async () => {
    renderApp(api({ agent: ["m2"] }, []), "/admin/llm");

    const section = await screen.findByRole("region", {
      name: "Agent fallback models",
    });
    expect(
      await within(section).findByLabelText("Fallback 1: backup-a"),
    ).toBeInTheDocument();
    const picker = within(section).getByLabelText("Add agent fallback model");
    const options = within(picker)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["Choose a tested model", "primary", "backup-b"]);
  });

  it("adds, reorders and removes models by saving the whole list", async () => {
    const puts: unknown[] = [];
    renderApp(api({ agent: ["m2"] }, puts), "/admin/llm");
    const section = await screen.findByRole("region", {
      name: "Agent fallback models",
    });
    await within(section).findByLabelText("Fallback 1: backup-a");

    fireEvent.change(
      within(section).getByLabelText("Add agent fallback model"),
      {
        target: { value: "m3" },
      },
    );
    fireEvent.click(
      within(section).getByRole("button", { name: "Add fallback" }),
    );
    await waitFor(() => expect(puts).toEqual([{ model_ids: ["m2", "m3"] }]));

    fireEvent.click(
      await within(section).findByRole("button", { name: "Move backup-b up" }),
    );
    await waitFor(() => expect(puts[1]).toEqual({ model_ids: ["m3", "m2"] }));

    fireEvent.click(
      await within(section).findByRole("button", { name: "Remove backup-b" }),
    );
    await waitFor(() => expect(puts[2]).toEqual({ model_ids: ["m2"] }));
  });
});
