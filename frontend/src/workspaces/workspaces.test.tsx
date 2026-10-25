import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { openSettings } from "../test/settingsDialog";
import { createApiClient } from "../api/client";
import { Toaster } from "../components/ui/sonner";
import { ApiClientContext } from "../api/context";
import type { Me } from "../api/queries";
import type { NewWorkspaceModels } from "../api/workspaceAi";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { currentLocation, setLocation } from "../test/navigation";
import { TestApp } from "../test/TestApp";

const CSRF_TOKEN = "csrf-token-from-the-cookie";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: [
    "workspace.edit",
    "workspace.leave",
    "workspace.manage_members",
    "workspace.transfer_ownership",
    "workspace.view",
  ],
  editor: ["workspace.leave", "workspace.view"],
  viewer: ["workspace.leave", "workspace.view"],
};

function workspace(
  id: string,
  name: string,
  role: WorkspaceRole,
  more: Partial<Workspace> = {},
) {
  return {
    id,
    name,
    description: "",
    domain: "",
    role,
    permissions: PERMISSIONS[role],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    ...more,
  } satisfies Workspace;
}

const RETAIL = workspace(
  "11111111-1111-4111-8111-111111111111",
  "Retail DW",
  "owner",
  {
    description: "The retail bank's warehouse",
    domain: "Retail banking",
  },
);
const CLAIMS = workspace(
  "22222222-2222-4222-8222-222222222222",
  "Claims",
  "viewer",
  {
    domain: "Insurance",
  },
);

interface Recorded {
  method: string;
  path: string;
  search: string;
  csrf: string | null;
  body: unknown;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiError(
  status: number,
  code: string,
  message: string,
  details = {},
): Response {
  return json({ error: { code, message, details } }, status);
}

/** A stand-in for the backend: Ada is signed in and belongs to `workspaces`. */
function fakeBackend(
  options: {
    workspaces?: Workspace[];
    pageSize?: number;
    createFails?: boolean;
    models?: NewWorkspaceModels;
  } = {},
) {
  const store = new Map(
    (options.workspaces ?? []).map((w) => [w.id, { ...w }]),
  );
  const pageSize = options.pageSize ?? 50;
  const requests: Recorded[] = [];

  function sorted() {
    return [...store.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  async function handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const text = await request.text();
    const body = text ? JSON.parse(text) : undefined;
    const csrf = request.headers.get("X-CSRF-Token");
    requests.push({
      method: request.method,
      path: url.pathname,
      search: url.search,
      csrf,
      body,
    });

    if (request.method !== "GET" && csrf !== CSRF_TOKEN) {
      return apiError(
        403,
        "csrf_failed",
        "The request has no valid CSRF token.",
      );
    }
    const lifecycle =
      /^\/api\/v1\/workspaces\/([^/]+)\/(archive|unarchive)$/.exec(
        url.pathname,
      );
    if (lifecycle && request.method === "POST") {
      const found = store.get(lifecycle[1]!);
      if (!found) {
        return apiError(404, "not_found", "Workspace not found.");
      }
      const archived = lifecycle[2] === "archive";
      store.set(found.id, {
        ...found,
        status: archived ? "archived" : "active",
        archived_at: archived ? "2026-02-01T09:00:00Z" : null,
      });
      return new Response(null, { status: 204 });
    }
    if (/^\/api\/v1\/workspaces\/[^/]+\/members$/.test(url.pathname)) {
      return json({ items: [] });
    }
    if (/^\/api\/v1\/workspaces\/[^/]+\/progress$/.test(url.pathname)) {
      return json({
        source_analysis: [],
        kpis: { status: "not_started" },
        dw_modeling: [],
      });
    }
    const one = /^\/api\/v1\/workspaces\/([^/]+)$/.exec(url.pathname)?.[1];
    if (one !== undefined) {
      const found = store.get(one);
      if (!found) {
        return apiError(404, "not_found", "Workspace not found.");
      }
      if (request.method === "GET") {
        return json(found);
      }
      if (request.method === "PATCH") {
        if (body.version !== found.version) {
          return apiError(
            409,
            "version_conflict",
            "Someone else changed this Workspace.",
            {
              current_version: found.version,
            },
          );
        }
        const { version, ...changes } = body;
        const edited = { ...found, ...changes, version: version + 1 };
        store.set(found.id, edited);
        return json(edited);
      }
      if (request.method === "DELETE") {
        if (body.name !== found.name) {
          return apiError(422, "name_mismatch", "The name does not match.");
        }
        store.delete(found.id);
        return new Response(null, { status: 204 });
      }
    }
    switch (`${request.method} ${url.pathname}`) {
      case "GET /api/v1/version":
        return json({ name: "DAWAM", version: "1.2.3" });
      case "GET /api/v1/me":
        return json(ADA);
      case "GET /api/v1/ai/new-workspace-models":
        return json(options.models ?? MODELS);
      case "GET /api/v1/workspaces": {
        const start = Number(url.searchParams.get("cursor") ?? 0);
        const items = sorted().slice(start, start + pageSize);
        const next =
          start + pageSize < store.size ? String(start + pageSize) : null;
        return json({ items, next_cursor: next });
      }
      case "POST /api/v1/workspaces": {
        if (options.createFails) {
          return apiError(422, "validation_error", "The request is not valid.");
        }
        const created = workspace(
          "33333333-3333-4333-8333-333333333333",
          body.name,
          "owner",
          {
            description: body.description,
            domain: body.domain,
          },
        );
        store.set(created.id, created);
        return json(created, 201);
      }
    }
    return apiError(404, "not_found", "Not Found");
  }

  /** Another member edits the Workspace, as if in another browser. */
  function editElsewhere(id: string, changes: Partial<Workspace>) {
    const found = store.get(id)!;
    store.set(id, { ...found, ...changes, version: found.version + 1 });
  }

  return { handle, requests, editElsewhere };
}

function renderApp(backend: ReturnType<typeof fakeBackend>, path = "/") {
  setLocation(path);
  const fetchStub = async (input: RequestInfo | URL, init?: RequestInit) =>
    backend.handle(new Request(input, init));
  const client = createApiClient({
    baseUrl: "http://dawam.test",
    fetch: fetchStub,
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <TestApp />
        <Toaster />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function fillIn(container: HTMLElement, label: string, value: string) {
  fireEvent.change(within(container).getByLabelText(label), {
    target: { value },
  });
}

beforeEach(() => {
  document.cookie = `dawam_csrf=${CSRF_TOKEN}; path=/`;
});

afterEach(() => {
  document.cookie = "dawam_csrf=; path=/; max-age=0";
});

describe("the Workspace list", () => {
  it("lists the user's Workspaces with their role in each", async () => {
    renderApp(fakeBackend({ workspaces: [RETAIL, CLAIMS] }));

    const table = await screen.findByRole("table");
    const rows = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) =>
        within(row)
          .getAllByRole("cell")
          .map((cell) => cell.textContent)
          .slice(0, 3),
      );
    expect(rows).toEqual([
      ["Claims", "Insurance", "Viewer"],
      ["Retail DW", "Retail banking", "Owner"],
    ]);
    expect(screen.getByRole("link", { name: "Retail DW" })).toHaveAttribute(
      "href",
      `/workspaces/${RETAIL.id}`,
    );
  });

  it("says so when the user belongs to no Workspace", async () => {
    renderApp(fakeBackend());

    expect(
      await screen.findByText(
        "You are not a member of any Workspace yet. Create one to get started.",
      ),
    ).toBeInTheDocument();
  });

  it("loads more Workspaces a page at a time", async () => {
    const backend = fakeBackend({ workspaces: [RETAIL, CLAIMS], pageSize: 1 });
    renderApp(backend);

    expect(
      await screen.findByRole("link", { name: "Claims" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Retail DW" }),
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(
      await screen.findByRole("link", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Claims" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
    expect(backend.requests.map((r) => r.search).filter(Boolean)).toContain(
      "?cursor=1",
    );
  });

  it("opens a Workspace from the list", async () => {
    renderApp(fakeBackend({ workspaces: [RETAIL] }));

    fireEvent.click(await screen.findByRole("link", { name: "Retail DW" }));

    expect(
      await screen.findByRole("heading", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(currentLocation()).toBe(`/workspaces/${RETAIL.id}`);
  });
});

const BGE_SMALL = "bbbbbbbb-0000-4000-8000-000000000001";
const BGE_LARGE = "bbbbbbbb-0000-4000-8000-000000000002";
const BIG = "bbbbbbbb-0000-4000-8000-000000000003";
const SMALL = "bbbbbbbb-0000-4000-8000-000000000004";

function local(id: string, name: string) {
  return { id, name, provider_name: "Local", internal: true };
}

const MODELS: NewWorkspaceModels = {
  internal_only: true,
  choices: {
    agent: [local(BIG, "big"), local(SMALL, "small")],
    light: [local(SMALL, "small")],
    embedding: [local(BGE_SMALL, "bge-small"), local(BGE_LARGE, "bge-large")],
    vision: [],
    rerank: [],
  },
};

describe("creating a Workspace", () => {
  it("asks for the default agent, light and embedding models and sends the choice", async () => {
    const backend = fakeBackend();
    renderApp(backend);

    fireEvent.click(
      await screen.findByRole("button", { name: "New workspace" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    const agent = await within(dialog).findByLabelText("Default agent model");
    expect(agent).toHaveValue(BIG);
    expect(within(dialog).getByLabelText("Light model")).toHaveValue("");
    expect(within(dialog).getByLabelText("Embedding model")).toHaveValue(
      BGE_SMALL,
    );
    expect(dialog).toHaveTextContent("only internal models are offered");
    fillIn(dialog, "Name", "Retail DW");
    fireEvent.change(agent, { target: { value: SMALL } });
    fireEvent.change(within(dialog).getByLabelText("Light model"), {
      target: { value: SMALL },
    });
    fireEvent.change(within(dialog).getByLabelText("Embedding model"), {
      target: { value: BGE_LARGE },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create Workspace" }),
    );

    await screen.findByRole("heading", { name: "Retail DW" });
    expect(
      backend.requests.find((r) => r.method === "POST")?.body,
    ).toMatchObject({
      name: "Retail DW",
      agent_model_id: SMALL,
      light_model_id: SMALL,
      embedding_model_id: BGE_LARGE,
    });
  });

  it("says document search is unavailable when no model has the Embedding role", async () => {
    const backend = fakeBackend({
      models: {
        internal_only: false,
        choices: {
          agent: [local(BIG, "big")],
          light: [],
          embedding: [],
          vision: [],
          rerank: [],
        },
      },
    });
    renderApp(backend);

    fireEvent.click(
      await screen.findByRole("button", { name: "New workspace" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    await within(dialog).findByLabelText("Default agent model");
    expect(
      within(dialog).queryByLabelText("Embedding model"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByLabelText("Light model"),
    ).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("document search is unavailable");
    fillIn(dialog, "Name", "Retail DW");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create Workspace" }),
    );

    await screen.findByRole("heading", { name: "Retail DW" });
    expect(
      backend.requests.find((r) => r.method === "POST")?.body,
    ).toMatchObject({ agent_model_id: BIG, embedding_model_id: null });
  });

  it("creates a Workspace and opens it, with the creator as its owner", async () => {
    const backend = fakeBackend();
    renderApp(backend);

    fireEvent.click(
      await screen.findByRole("button", { name: "New workspace" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    fillIn(dialog, "Name", "Retail DW");
    fillIn(dialog, "Description", "The retail bank's warehouse");
    fillIn(dialog, "Business domain", "Retail banking");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create Workspace" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Your role: Owner")).toBeInTheDocument();
    expect(currentLocation()).toBe(
      "/workspaces/33333333-3333-4333-8333-333333333333",
    );
    expect(backend.requests.find((r) => r.method === "POST")).toMatchObject({
      path: "/api/v1/workspaces",
      csrf: CSRF_TOKEN,
      body: {
        name: "Retail DW",
        description: "The retail bank's warehouse",
        domain: "Retail banking",
      },
    });

    fireEvent.click(screen.getByRole("button", { name: /Switch Workspace/ }));
    fireEvent.click(
      await screen.findByRole("link", { name: "All Workspaces" }),
    );
    expect(
      await screen.findByRole("link", { name: "Retail DW" }),
    ).toBeInTheDocument();
  });

  it("shows the API's message when the Workspace cannot be created", async () => {
    renderApp(fakeBackend({ createFails: true }));

    fireEvent.click(
      await screen.findByRole("button", { name: "New workspace" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New workspace" });
    fillIn(dialog, "Name", "Retail DW");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create Workspace" }),
    );

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The request is not valid.",
    );
    expect(currentLocation()).toBe("/");
  });
});

describe("a Workspace's row in the list", () => {
  const OWNED = workspace(RETAIL.id, "Retail DW", "owner", {
    domain: "Retail banking",
    permissions: [
      ...PERMISSIONS.owner,
      "workspace.archive",
      "workspace.delete",
    ],
  });

  it("lets an owner edit the details in a popup", async () => {
    const backend = fakeBackend({ workspaces: [OWNED] });
    renderApp(backend);

    const row = await screen.findByRole("row", { name: "Retail DW" });
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Edit workspace",
    });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("Retail DW");
    fillIn(dialog, "Name", "Retail warehouse");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "Retail warehouse" }),
    ).toBeInTheDocument();
    expect(backend.requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `/api/v1/workspaces/${RETAIL.id}`,
      body: { version: 1, name: "Retail warehouse" },
    });
  });

  it("unarchives an archived Workspace", async () => {
    const backend = fakeBackend({
      workspaces: [
        {
          ...OWNED,
          permissions: [...OWNED.permissions, "workspace.unarchive"].filter(
            (action) => action !== "workspace.archive",
          ) as Workspace["permissions"],
          status: "archived",
        },
      ],
    });
    renderApp(backend);

    fireEvent.click(await screen.findByRole("button", { name: "Unarchive" }));

    await waitFor(() =>
      expect(backend.requests.some((r) => r.path.endsWith("/unarchive"))).toBe(
        true,
      ),
    );
  });

  it("offers an editor or viewer no row actions", async () => {
    renderApp(fakeBackend({ workspaces: [CLAIMS] }));

    const row = await screen.findByRole("row", { name: "Claims" });
    expect(within(row).queryByRole("button")).not.toBeInTheDocument();
  });

  it("archives an owned Workspace", async () => {
    const backend = fakeBackend({ workspaces: [OWNED] });
    renderApp(backend);

    fireEvent.click(await screen.findByRole("button", { name: "Archive" }));

    await waitFor(() =>
      expect(backend.requests.some((r) => r.path.endsWith("/archive"))).toBe(
        true,
      ),
    );
  });

  it("deletes only once the name is typed in the confirmation", async () => {
    const backend = fakeBackend({ workspaces: [OWNED] });
    renderApp(backend);

    const row = await screen.findByRole("row", { name: "Retail DW" });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Delete Retail DW?",
    });
    const confirm = within(dialog).getByRole("button", {
      name: "Delete Workspace",
    });
    expect(confirm).toBeDisabled();
    fillIn(dialog, "Workspace name", "Retail DW");
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() =>
      expect(
        screen.queryByRole("link", { name: "Retail DW" }),
      ).not.toBeInTheDocument(),
    );
    expect(backend.requests.find((r) => r.method === "DELETE")).toMatchObject({
      path: `/api/v1/workspaces/${RETAIL.id}`,
      body: { name: "Retail DW" },
    });
  });
});

describe("a Workspace's page", () => {
  it("lets an owner edit the details", async () => {
    const backend = fakeBackend({ workspaces: [RETAIL] });
    renderApp(backend, `/workspaces/${RETAIL.id}`);

    await openSettings();
    expect(await screen.findByLabelText("Name")).toHaveValue("Retail DW");
    fill("Name", "Retail warehouse");
    fill("Business domain", "Banking");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", {
        name: "Retail warehouse",
        hidden: true,
      }),
    ).toBeInTheDocument();
    expect(backend.requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `/api/v1/workspaces/${RETAIL.id}`,
      csrf: CSRF_TOKEN,
      body: {
        version: 1,
        name: "Retail warehouse",
        description: "The retail bank's warehouse",
        domain: "Banking",
      },
    });
  });

  it("tells an owner someone else changed the Workspace first, and reloads it", async () => {
    const backend = fakeBackend({ workspaces: [RETAIL] });
    renderApp(backend, `/workspaces/${RETAIL.id}`);
    await openSettings();
    await screen.findByLabelText("Name");
    backend.editElsewhere(RETAIL.id, { name: "Renamed elsewhere" });

    fill("Name", "My rename");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Someone else changed this Workspace since you opened it.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Name")).toHaveValue("Renamed elsewhere"),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fill("Name", "My rename");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(
      backend.requests.filter((r) => r.method === "PATCH").at(-1)?.body,
    ).toMatchObject({
      version: 2,
      name: "My rename",
    });
  });

  it.each([
    ["editor", "Editor"],
    ["viewer", "Viewer"],
  ] as const)("shows the details read-only to an %s", async (role, label) => {
    const shared = { ...RETAIL, role, permissions: PERMISSIONS[role] };
    renderApp(
      fakeBackend({ workspaces: [shared] }),
      `/workspaces/${RETAIL.id}`,
    );

    expect(
      await screen.findByRole("heading", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(screen.getByText(`Your role: ${label}`)).toBeInTheDocument();
    const dialog = await openSettings();
    expect(
      within(dialog).queryByRole("tab", { name: "Archive & delete" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("The retail bank's warehouse")).toBeInTheDocument();
    expect(screen.getByText("Retail banking")).toBeInTheDocument();
    expect(
      screen.getByText("Only owners can edit these details."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Save" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the settings panels off the page, behind a button beside Assistant", async () => {
    const archivable = {
      ...RETAIL,
      permissions: [...PERMISSIONS.owner, "workspace.archive"],
    } as Workspace;
    renderApp(
      fakeBackend({ workspaces: [archivable] }),
      `/workspaces/${RETAIL.id}`,
    );

    await screen.findByRole("button", { name: "Workspace settings" });
    expect(screen.getByRole("button", { name: "Assistant" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
    expect(screen.queryByText("AI settings")).not.toBeInTheDocument();
    expect(screen.queryByText("Archive and delete")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Members" })).toBeInTheDocument();

    const dialog = await openSettings();
    expect(within(dialog).getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Details",
      "AI",
      "Archive & delete",
    ]);
  });

  it("shows a not-found page for a Workspace the API does not show the user", async () => {
    renderApp(
      fakeBackend({ workspaces: [RETAIL] }),
      `/workspaces/${CLAIMS.id}`,
    );

    expect(
      await screen.findByRole("heading", { name: "Workspace not found" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("It does not exist, or you are not a member of it."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("link", { name: "Back to your Workspaces" }),
    );
    expect(
      await screen.findByRole("link", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(currentLocation()).toBe("/");
  });
});
