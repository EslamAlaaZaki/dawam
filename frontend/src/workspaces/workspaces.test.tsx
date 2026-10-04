import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { currentLocation, setLocation } from "../test/navigation";
import { TestApp } from "../test/TestApp";

const CSRF_TOKEN = "csrf-token-from-the-cookie";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: ["workspace.edit", "workspace.manage_members", "workspace.view"],
  editor: ["workspace.view"],
  viewer: ["workspace.view"],
};

function workspace(id: string, name: string, role: WorkspaceRole, more: Partial<Workspace> = {}) {
  return {
    id,
    name,
    description: "",
    domain: "",
    role,
    permissions: PERMISSIONS[role],
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    ...more,
  } satisfies Workspace;
}

const RETAIL = workspace("11111111-1111-4111-8111-111111111111", "Retail DW", "owner", {
  description: "The retail bank's warehouse",
  domain: "Retail banking",
});
const CLAIMS = workspace("22222222-2222-4222-8222-222222222222", "Claims", "viewer", {
  domain: "Insurance",
});

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

function apiError(status: number, code: string, message: string, details = {}): Response {
  return json({ error: { code, message, details } }, status);
}

/** A stand-in for the backend: Ada is signed in and belongs to `workspaces`. */
function fakeBackend(
  options: { workspaces?: Workspace[]; pageSize?: number; createFails?: boolean } = {},
) {
  const store = new Map((options.workspaces ?? []).map((w) => [w.id, { ...w }]));
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
    requests.push({ method: request.method, path: url.pathname, search: url.search, csrf, body });

    if (request.method !== "GET" && csrf !== CSRF_TOKEN) {
      return apiError(403, "csrf_failed", "The request has no valid CSRF token.");
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
          return apiError(409, "version_conflict", "Someone else changed this Workspace.", {
            current_version: found.version,
          });
        }
        const { version, ...changes } = body;
        const edited = { ...found, ...changes, version: version + 1 };
        store.set(found.id, edited);
        return json(edited);
      }
    }
    switch (`${request.method} ${url.pathname}`) {
      case "GET /api/v1/version":
        return json({ name: "DAWAM", version: "1.2.3" });
      case "GET /api/v1/me":
        return json(ADA);
      case "GET /api/v1/workspaces": {
        const start = Number(url.searchParams.get("cursor") ?? 0);
        const items = sorted().slice(start, start + pageSize);
        const next = start + pageSize < store.size ? String(start + pageSize) : null;
        return json({ items, next_cursor: next });
      }
      case "POST /api/v1/workspaces": {
        if (options.createFails) {
          return apiError(422, "validation_error", "The request is not valid.");
        }
        const created = workspace("33333333-3333-4333-8333-333333333333", body.name, "owner", {
          description: body.description,
          domain: body.domain,
        });
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
  const client = createApiClient({ baseUrl: "http://dawam.test", fetch: fetchStub });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <TestApp />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
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
      .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));
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
      await screen.findByText("You are not a member of any Workspace yet. Create one to get started."),
    ).toBeInTheDocument();
  });

  it("loads more Workspaces a page at a time", async () => {
    const backend = fakeBackend({ workspaces: [RETAIL, CLAIMS], pageSize: 1 });
    renderApp(backend);

    expect(await screen.findByRole("link", { name: "Claims" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Retail DW" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show more" }));

    expect(await screen.findByRole("link", { name: "Retail DW" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Claims" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
    expect(backend.requests.map((r) => r.search).filter(Boolean)).toContain("?cursor=1");
  });

  it("opens a Workspace from the list", async () => {
    renderApp(fakeBackend({ workspaces: [RETAIL] }));

    fireEvent.click(await screen.findByRole("link", { name: "Retail DW" }));

    expect(await screen.findByRole("heading", { name: "Retail DW" })).toBeInTheDocument();
    expect(currentLocation()).toBe(`/workspaces/${RETAIL.id}`);
  });
});

describe("creating a Workspace", () => {
  it("creates a Workspace and opens it, with the creator as its owner", async () => {
    const backend = fakeBackend();
    renderApp(backend);

    fireEvent.click(await screen.findByRole("link", { name: "New Workspace" }));
    expect(await screen.findByRole("heading", { name: "New Workspace" })).toBeInTheDocument();
    fill("Name", "Retail DW");
    fill("Description", "The retail bank's warehouse");
    fill("Business domain", "Retail banking");
    fireEvent.click(screen.getByRole("button", { name: "Create Workspace" }));

    expect(await screen.findByRole("heading", { name: "Retail DW" })).toBeInTheDocument();
    expect(screen.getByText("Your role: Owner")).toBeInTheDocument();
    expect(currentLocation()).toBe("/workspaces/33333333-3333-4333-8333-333333333333");
    expect(backend.requests.find((r) => r.method === "POST")).toMatchObject({
      path: "/api/v1/workspaces",
      csrf: CSRF_TOKEN,
      body: {
        name: "Retail DW",
        description: "The retail bank's warehouse",
        domain: "Retail banking",
      },
    });

    fireEvent.click(screen.getByRole("link", { name: "All Workspaces" }));
    expect(await screen.findByRole("link", { name: "Retail DW" })).toBeInTheDocument();
  });

  it("shows the API's message when the Workspace cannot be created", async () => {
    renderApp(fakeBackend({ createFails: true }), "/workspaces/new");

    await screen.findByLabelText("Name");
    fill("Name", "Retail DW");
    fireEvent.click(screen.getByRole("button", { name: "Create Workspace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The request is not valid.");
    expect(currentLocation()).toBe("/workspaces/new");
  });
});

describe("a Workspace's page", () => {
  it("lets an owner edit the details", async () => {
    const backend = fakeBackend({ workspaces: [RETAIL] });
    renderApp(backend, `/workspaces/${RETAIL.id}`);

    expect(await screen.findByLabelText("Name")).toHaveValue("Retail DW");
    fill("Name", "Retail warehouse");
    fill("Business domain", "Banking");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(screen.getByRole("heading", { name: "Retail warehouse" })).toBeInTheDocument();
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
    await screen.findByLabelText("Name");
    backend.editElsewhere(RETAIL.id, { name: "Renamed elsewhere" });

    fill("Name", "My rename");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Someone else changed this Workspace since you opened it.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));

    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Renamed elsewhere"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fill("Name", "My rename");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(backend.requests.filter((r) => r.method === "PATCH").at(-1)?.body).toMatchObject({
      version: 2,
      name: "My rename",
    });
  });

  it.each([
    ["editor", "Editor"],
    ["viewer", "Viewer"],
  ] as const)("shows the details read-only to an %s", async (role, label) => {
    const shared = { ...RETAIL, role, permissions: PERMISSIONS[role] };
    renderApp(fakeBackend({ workspaces: [shared] }), `/workspaces/${RETAIL.id}`);

    expect(await screen.findByRole("heading", { name: "Retail DW" })).toBeInTheDocument();
    expect(screen.getByText(`Your role: ${label}`)).toBeInTheDocument();
    expect(screen.getByText("The retail bank's warehouse")).toBeInTheDocument();
    expect(screen.getByText("Retail banking")).toBeInTheDocument();
    expect(screen.getByText("Only owners can edit these details.")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  it("shows a not-found page for a Workspace the API does not show the user", async () => {
    renderApp(fakeBackend({ workspaces: [RETAIL] }), `/workspaces/${CLAIMS.id}`);

    expect(await screen.findByRole("heading", { name: "Workspace not found" })).toBeInTheDocument();
    expect(screen.getByText("It does not exist, or you are not a member of it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Back to your Workspaces" }));
    expect(await screen.findByRole("link", { name: "Retail DW" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/");
  });
});
