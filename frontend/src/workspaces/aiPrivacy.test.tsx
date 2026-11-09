import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { WorkspaceAiSettings } from "../api/workspaceAi";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { openSettings } from "../test/settingsDialog";
import {
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

function settings(over: Partial<WorkspaceAiSettings> = {}): WorkspaceAiSettings {
  return {
    internal_only: false,
    share_profiles: false,
    share_documents: false,
    share_samples: false,
    auto_retrieve: false,
    agent_models: [],
    light_models: [],
    light_features: [],
    embedding_model: null,
    embedding_rebuilding: false,
    vision_model: null,
    rerank_model: null,
    choices: { agent: [], light: [], embedding: [], vision: [], rerank: [] },
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

/** The owner's backend: PUT stores the three kinds it is sent. */
function ownerBackend(start: Partial<WorkspaceAiSettings> = {}) {
  let current = settings(start);
  return renderApp(
    backend("owner", (r) => {
      if (r.path !== AI) {
        return undefined;
      }
      if (r.method === "PUT") {
        current = { ...current, ...(r.body as Partial<WorkspaceAiSettings>) };
      }
      return json(current);
    }),
    PATH,
  );
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Workspace AI privacy tab", () => {
  it("always shares metadata and starts with the other kinds off", async () => {
    ownerBackend();

    await openSettings("AI privacy");
    const card = await screen.findByRole("form", { name: "AI privacy" });

    expect(card).toHaveTextContent("Metadata");
    expect(card).toHaveTextContent("Always shared");
    for (const name of ["Profile statistics", "Documents", "Sample rows"]) {
      expect(within(card).getByRole("switch", { name })).not.toBeChecked();
    }
  });

  it("asks before turning a kind on, and lets Documents be on without Profile statistics", async () => {
    const requests = ownerBackend();

    await openSettings("AI privacy");
    const card = await screen.findByRole("form", { name: "AI privacy" });
    fireEvent.click(within(card).getByRole("switch", { name: "Documents" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Share documents with the AI?",
    });
    expect(
      within(card).getByRole("switch", { name: "Documents", hidden: true }),
    ).not.toBeChecked();
    fireEvent.click(within(dialog).getByRole("button", { name: "Turn on" }));
    expect(
      await within(card).findByRole("switch", { name: "Documents" }),
    ).toBeChecked();
    expect(
      within(card).getByRole("switch", { name: "Profile statistics" }),
    ).not.toBeChecked();
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      internal_only: false,
      share_profiles: false,
      share_documents: true,
      share_samples: false,
    });
  });

  it("does not turn a kind on when the confirmation is cancelled", async () => {
    ownerBackend();

    await openSettings("AI privacy");
    const card = await screen.findByRole("form", { name: "AI privacy" });
    fireEvent.click(within(card).getByRole("switch", { name: "Sample rows" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Share sample rows with the AI?",
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(
      await within(card).findByRole("switch", { name: "Sample rows" }),
    ).not.toBeChecked();
  });

  it("turns a kind off without asking", async () => {
    const requests = ownerBackend({
      share_profiles: true,
      share_samples: true,
    });

    await openSettings("AI privacy");
    const card = await screen.findByRole("form", { name: "AI privacy" });
    fireEvent.click(within(card).getByRole("switch", { name: "Sample rows" }));

    expect(
      screen.queryByRole("dialog", { name: /Share .* with the AI\?/ }),
    ).not.toBeInTheDocument();
    expect(
      within(card).getByRole("switch", { name: "Sample rows" }),
    ).not.toBeChecked();
    fireEvent.click(within(card).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toMatchObject({
      share_profiles: true,
      share_samples: false,
    });
  });

  it("is read-only to editors and viewers", async () => {
    renderApp(
      backend("editor", (r) =>
        r.path === AI ? json(settings({ share_documents: true })) : undefined,
      ),
      PATH,
    );

    await openSettings("AI privacy");
    const card = await screen.findByRole("region", { name: "AI privacy" });

    expect(card).toHaveTextContent("Documents");
    expect(card).toHaveTextContent(
      "Only owners can change what the AI may see.",
    );
    expect(within(card).queryByRole("switch")).not.toBeInTheDocument();
    expect(
      within(card).queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
  });

  it("is read-only in an archived Workspace, for owners too", async () => {
    renderApp(
      backend(
        "owner",
        (r) => (r.path === AI ? json(settings()) : undefined),
        "archived",
      ),
      PATH,
    );

    await openSettings("AI privacy");
    const card = await screen.findByRole("region", { name: "AI privacy" });

    expect(card).toHaveTextContent("archived");
    expect(within(card).queryByRole("switch")).not.toBeInTheDocument();
  });
});
