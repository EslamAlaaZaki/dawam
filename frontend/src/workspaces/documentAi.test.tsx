import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { KnobInfo, WorkspaceDocumentAi } from "../api/documentAi";
import type { Me } from "../api/queries";
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
const DOC_AI = `/api/v1/workspaces/${WORKSPACE_ID}/document-ai`;

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

const KNOBS: KnobInfo[] = [
  {
    key: "chunk_size",
    group: "chunking",
    kind: "int",
    label: "Chunk size",
    description: "Target tokens per chunk.",
    owner: true,
    reindex: true,
    default: 250,
    min: 50,
    max: 1000,
    choices: [],
  },
  {
    key: "top_k",
    group: "retrieval",
    kind: "int",
    label: "Results",
    description: "Passages a search returns.",
    owner: true,
    reindex: false,
    default: 10,
    min: 1,
    max: 50,
    choices: [],
  },
];

function settings(
  over: Partial<WorkspaceDocumentAi> = {},
): WorkspaceDocumentAi {
  return {
    version: 2,
    updated_at: null,
    overrides: { top_k: 5 },
    inherited: { chunk_size: 250, top_k: 10 },
    effective: { chunk_size: 250, top_k: 5 },
    bounds: { chunk_size: { min: 100, max: 400 }, top_k: { min: 1, max: 20 } },
    knobs: KNOBS,
    ...over,
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
      case `GET ${DOC_AI}`:
        return json(settings());
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

describe("the Workspace's Document AI settings", () => {
  it("shows the effective values read-only to non-owners", async () => {
    renderApp(
      backend("viewer", () => undefined),
      PATH,
    );

    const dialog = await openSettings("Document AI");

    expect(await within(dialog).findByText("Chunk size")).toBeInTheDocument();
    expect(within(dialog).getByText("250")).toBeInTheDocument();
    expect(within(dialog).getByText("(this Workspace)")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Only owners can override these settings."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Save/ })).toBeNull();
  });

  it("lets owners override a knob and inherit another, with the version they read", async () => {
    const requests = renderApp(
      backend("owner", (r) =>
        r.method === "PUT" && r.path === DOC_AI
          ? json(
              settings({
                version: 3,
                overrides: { chunk_size: 300 },
                effective: { chunk_size: 300, top_k: 10 },
              }),
            )
          : undefined,
      ),
      PATH,
    );

    const dialog = await openSettings("Document AI");
    const size = await within(dialog).findByLabelText("Chunk size");
    expect(size).toBeDisabled();
    expect(size).toHaveValue(250);
    expect(
      within(dialog).getByText("Installation value: 250"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/Allowed: 100 to 400\./),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Results")).toHaveValue(5);

    fireEvent.click(within(dialog).getByLabelText("Override Chunk size"));
    fireEvent.change(within(dialog).getByLabelText("Chunk size"), {
      target: { value: "300" },
    });
    fireEvent.click(within(dialog).getByLabelText("Override Results"));
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save Document AI settings" }),
    );

    expect(
      await screen.findByText("Document AI settings saved."),
    ).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      version: 2,
      overrides: { chunk_size: 300 },
    });
    expect(
      await within(dialog).findByText(/Re-index the Workspace's documents/),
    ).toBeInTheDocument();
  });

  async function saveChunkSize(role: WorkspaceRole) {
    const requests = renderApp(
      backend(role, (r) => {
        if (r.method === "PUT" && r.path === DOC_AI) {
          return json(
            settings({
              version: 3,
              overrides: { chunk_size: 300 },
              effective: { chunk_size: 300, top_k: 5 },
            }),
          );
        }
        if (
          r.method === "POST" &&
          r.path === `/api/v1/workspaces/${WORKSPACE_ID}/documents/reindex`
        ) {
          return json({ job_id: "j1", status: "queued" }, 202);
        }
        return undefined;
      }),
      PATH,
    );
    const dialog = await openSettings("Document AI");
    fireEvent.click(
      await within(dialog).findByLabelText("Override Chunk size"),
    );
    fireEvent.change(within(dialog).getByLabelText("Chunk size"), {
      target: { value: "300" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save Document AI settings" }),
    );
    await within(dialog).findByText(/Re-index the Workspace's documents/);
    return { requests, dialog };
  }

  it("queues a re-index when the owner accepts the prompt", async () => {
    const { requests, dialog } = await saveChunkSize("owner");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Re-index now" }),
    );
    expect(
      await screen.findByText("Re-indexing all documents."),
    ).toBeInTheDocument();
    expect(
      requests.filter((r) => r.path.endsWith("/documents/reindex")),
    ).toHaveLength(1);
  });

  it("leaves the index as is when the owner declines", async () => {
    const { requests, dialog } = await saveChunkSize("owner");
    fireEvent.click(within(dialog).getByRole("button", { name: "Not now" }));
    expect(
      within(dialog).queryByText(/Re-index the Workspace's documents/),
    ).not.toBeInTheDocument();
    expect(requests.some((r) => r.path.endsWith("/documents/reindex"))).toBe(
      false,
    );
  });

  it("shows the API's message when a save is refused", async () => {
    renderApp(
      backend("owner", (r) =>
        r.method === "PUT"
          ? apiError(
              409,
              "settings_changed",
              "Someone changed these settings meanwhile.",
            )
          : undefined,
      ),
      PATH,
    );

    const dialog = await openSettings("Document AI");
    fireEvent.change(await within(dialog).findByLabelText("Results"), {
      target: { value: "15" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Save Document AI settings" }),
    );

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Someone changed these settings meanwhile.",
    );
  });
});
