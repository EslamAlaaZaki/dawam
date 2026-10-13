import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { WorkspaceAiSettings } from "../api/workspaceAi";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
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
const EMBED_LESS = { internal: false, provider_name: "Cloud" };

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

/** The installation uses "Big" for the agent and "Small" for light tasks. */
function settings(
  own: { agent?: string; light?: string } = {},
): WorkspaceAiSettings {
  const name = (id: string) => (id === BIG ? "Big" : "Small");
  const agent = own.agent ?? BIG;
  const light = own.light ?? SMALL;
  return {
    internal_only: false,
    data_sharing_level: "metadata",
    agent_model_id: own.agent ?? null,
    light_model_id: own.light ?? null,
    effective_agent_model_id: agent,
    effective_agent_model_name: name(agent),
    effective_light_model_id: light,
    effective_light_model_name: name(light),
    approved_models: [
      { id: BIG, name: "Big", ...EMBED_LESS },
      { id: SMALL, name: "Small", ...EMBED_LESS },
    ],
    light_models: [
      { id: BIG, name: "Big", ...EMBED_LESS },
      { id: SMALL, name: "Small", ...EMBED_LESS },
    ],
    internal_agent_available: false,
    banner: false,
  };
}

function backend(
  role: WorkspaceRole,
  handle: (r: ApiRequest) => Response | undefined,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
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
  it("shows the effective model for each role, read-only for non-owners", async () => {
    renderApp(
      backend("viewer", (r) =>
        r.method === "GET" && r.path === AI
          ? json(settings({ light: BIG }))
          : undefined,
      ),
      PATH,
    );

    const card = await screen.findByRole("region", { name: "AI settings" });
    expect(card).toHaveTextContent("Agent modelBig (installation default)");
    expect(card).toHaveTextContent("Light modelBig");
    expect(card).not.toHaveTextContent("Light modelBig (installation default)");
    expect(
      within(card).queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
  });

  it("lets an owner choose the light model and clear the agent model", async () => {
    let current = settings({ agent: BIG });
    const requests = renderApp(
      backend("owner", (r) => {
        if (r.path !== AI) {
          return undefined;
        }
        if (r.method === "PUT") {
          const body = r.body as {
            agent_model_id: string | null;
            light_model_id: string | null;
          };
          current = settings({
            agent: body.agent_model_id ?? undefined,
            light: body.light_model_id ?? undefined,
          });
        }
        return json(current);
      }),
      PATH,
    );

    const card = await screen.findByRole("form", { name: "AI settings" });
    fireEvent.change(within(card).getByLabelText("Agent model"), {
      target: { value: "" },
    });
    fireEvent.change(within(card).getByLabelText("Light model"), {
      target: { value: BIG },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    const put = requests.find((r) => r.method === "PUT");
    expect(put?.body).toMatchObject({
      agent_model_id: null,
      light_model_id: BIG,
    });
    await waitFor(() =>
      expect(
        screen.getByRole("form", { name: "AI settings" }),
      ).toHaveTextContent("Agent modelBig (installation default)"),
    );
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
              "That model is not in the Workspace's Allowed Models.",
            )
          : json(settings());
      }),
      PATH,
    );

    const card = await screen.findByRole("form", { name: "AI settings" });
    fireEvent.change(within(card).getByLabelText("Light model"), {
      target: { value: BIG },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(
      await within(card).findByText(/not in the Workspace's Allowed Models/),
    ).toBeInTheDocument();
  });
});
