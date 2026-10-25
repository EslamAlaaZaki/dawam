import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { WorkspaceAiSettings } from "../api/workspaceAi";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { openSettings } from "../test/settingsDialog";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const AI = `/api/v1/workspaces/${WORKSPACE_ID}/ai-settings`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const BIG = "aaaaaaaa-0000-4000-8000-000000000001";
const SMALL = "aaaaaaaa-0000-4000-8000-000000000002";
const RERANK = "aaaaaaaa-0000-4000-8000-000000000009";
const EMBED = "aaaaaaaa-0000-4000-8000-000000000003";
const LOCAL = "aaaaaaaa-0000-4000-8000-000000000004";
const VISION = "aaaaaaaa-0000-4000-8000-000000000005";

function choice(id: string, name: string, internal = false) {
  return { id, name, provider_name: internal ? "Local" : "Cloud", internal };
}

function chosen(id: string, name: string, isDefault: boolean) {
  return { ...choice(id, name), is_default: isDefault, limited: false };
}

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions:
      role === "owner"
        ? ["workspace.edit", "workspace.leave", "workspace.view"]
        : ["workspace.leave", "workspace.view"],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

/** The Workspace uses Big (default) and Small as agent models, Small for light tasks. */
function settings(
  over: Partial<WorkspaceAiSettings> = {},
): WorkspaceAiSettings {
  return {
    internal_only: false,
    data_sharing_level: "metadata",
    chunking_strategy: "headings",
    auto_retrieve: false,
    agent_models: [chosen(BIG, "Big", true), chosen(SMALL, "Small", false)],
    light_models: [chosen(SMALL, "Small", true)],
    light_features: [
      {
        key: "conversation_compaction",
        name: "Conversation compaction",
        description: "Summarises the oldest turns.",
        enabled: true,
      },
    ],
    embedding_model: chosen(EMBED, "Embed-1", true),
    embedding_rebuilding: false,
    vision_model: null,
    rerank_model: null,
    choices: {
      agent: [
        choice(BIG, "Big"),
        choice(LOCAL, "Local", true),
        choice(SMALL, "Small"),
      ],
      light: [choice(SMALL, "Small")],
      embedding: [choice(EMBED, "Embed-1")],
      vision: [choice(VISION, "Qwen-VL", true)],
      rerank: [choice(RERANK, "bge-reranker-v2-m3", true)],
    },
    internal_agent_available: true,
    banner: false,
    ...over,
  };
}

function backend(
  role: WorkspaceRole,
  handle: (r: ApiRequest) => Response | undefined,
  status: Workspace["status"] = "active",
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json({ ...workspace(role), status });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      default:
        return handle(r);
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Workspace AI settings card", () => {
  it("shows the Workspace Models read-only to non-owners", async () => {
    renderApp(
      backend("viewer", (r) =>
        r.method === "GET" && r.path === AI ? json(settings()) : undefined,
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("region", { name: "AI settings" });
    expect(card).toHaveTextContent("Agent modelsBig (default), Small");
    expect(card).toHaveTextContent("Light modelsSmall (default)");
    expect(card).toHaveTextContent("Embedding modelEmbed-1");
    expect(card).toHaveTextContent("Only owners can choose the models.");
    expect(
      within(card).queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
  });

  it("lists the light-role features, off when no light model is assigned", async () => {
    const off = settings({
      light_models: [],
      light_features: [
        {
          key: "conversation_compaction",
          name: "Conversation compaction",
          description: "Summarises the oldest turns.",
          enabled: false,
        },
      ],
    });
    renderApp(
      backend("owner", (r) =>
        r.method === "GET" && r.path === AI ? json(off) : undefined,
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const features = within(card).getByRole("region", {
      name: "Light-role features",
    });
    expect(features).toHaveTextContent("Conversation compaction");
    expect(features).toHaveTextContent("Off");
  });

  it("is read-only in an archived Workspace, for owners too", async () => {
    renderApp(
      backend(
        "owner",
        (r) =>
          r.method === "GET" && r.path === AI ? json(settings()) : undefined,
        "archived",
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("region", { name: "AI settings" });
    expect(card).toHaveTextContent("archived");
    expect(within(card).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("lets an owner choose agent and light models with a default each, and the embedding model", async () => {
    let current = settings();
    const requests = renderApp(
      backend("owner", (r) => {
        if (r.path !== AI) {
          return undefined;
        }
        if (r.method === "PUT") {
          current = settings({
            agent_models: [chosen(SMALL, "Small", true)],
            light_models: [],
            embedding_model: null,
          });
        }
        return json(current);
      }),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const agents = within(card).getByRole("group", { name: "Agent models" });
    expect(
      within(agents).getByRole("radio", { name: "Default agent model: Big" }),
    ).toBeChecked();
    fireEvent.click(
      within(agents).getByRole("radio", { name: "Default agent model: Small" }),
    );
    fireEvent.click(within(agents).getByRole("checkbox", { name: /^Big/ }));
    const lights = within(card).getByRole("group", { name: "Light models" });
    fireEvent.click(within(lights).getByRole("checkbox", { name: /^Small/ }));
    expect(lights).toHaveTextContent("the light-role features are off");
    fireEvent.change(within(card).getByLabelText("Embedding model"), {
      target: { value: "" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      internal_only: false,
      data_sharing_level: "metadata",
      agent_model_ids: [SMALL],
      default_agent_model_id: SMALL,
      light_model_ids: [],
      default_light_model_id: null,
      embedding_model_id: null,
      vision_model_id: null,
      rerank_model_id: null,
    });
  });

  it("lets an owner choose the vision model that reads scanned pages", async () => {
    const requests = renderApp(
      backend("owner", (r) => (r.path === AI ? json(settings()) : undefined)),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const picker = within(card).getByLabelText("Vision model");
    expect(picker).toHaveValue("");
    expect(card).toHaveTextContent(
      "only when the data-sharing level allows documents",
    );
    fireEvent.change(picker, { target: { value: VISION } });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toMatchObject({
      vision_model_id: VISION,
    });
  });

  it("lets an owner choose the rerank model that reorders search results", async () => {
    const requests = renderApp(
      backend("owner", (r) => (r.path === AI ? json(settings()) : undefined)),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const picker = within(card).getByLabelText("Rerank model");
    expect(picker).toHaveValue("");
    fireEvent.change(picker, { target: { value: RERANK } });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toMatchObject({
      rerank_model_id: RERANK,
    });
  });

  it("shows the vision model read-only to non-owners", async () => {
    renderApp(
      backend("viewer", (r) =>
        r.method === "GET" && r.path === AI
          ? json(settings({ vision_model: chosen(VISION, "Qwen-VL", true) }))
          : undefined,
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("region", { name: "AI settings" });
    expect(card).toHaveTextContent("Vision modelQwen-VL");
  });

  it("offers an internal-only Workspace internal models only", async () => {
    renderApp(
      backend("owner", (r) =>
        r.method === "GET" && r.path === AI
          ? json(
              settings({
                internal_only: true,
                agent_models: [
                  { ...chosen(LOCAL, "Local", true), internal: true },
                ],
                light_models: [],
              }),
            )
          : undefined,
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const agents = within(card).getByRole("group", { name: "Agent models" });
    expect(
      within(agents)
        .getAllByRole("checkbox")
        .map((c) => c.parentElement?.textContent),
    ).toEqual(["Local (Local)"]);
    expect(card).toHaveTextContent("only internal models are offered");
  });

  it("says while the documents are embedded again with a new model", async () => {
    renderApp(
      backend("owner", (r) =>
        r.method === "GET" && r.path === AI
          ? json(settings({ embedding_rebuilding: true }))
          : undefined,
      ),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    const picker = within(card).getByLabelText("Embedding model");
    expect(picker).toBeEnabled();
    expect(picker).toHaveValue(EMBED);
    expect(card).toHaveTextContent("being embedded again with this model");
  });

  it("shows why a choice was refused", async () => {
    renderApp(
      backend("owner", (r) => {
        if (r.path !== AI) {
          return undefined;
        }
        return r.method === "PUT"
          ? apiError(
              422,
              "model_not_allowed",
              "An internal-only Workspace uses only models on internal providers.",
            )
          : json(settings());
      }),
      PATH,
    );

    await openSettings("AI");
    const card = await screen.findByRole("form", { name: "AI settings" });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(
      await within(card).findByText(/only models on internal providers/),
    ).toBeInTheDocument();
  });
});
