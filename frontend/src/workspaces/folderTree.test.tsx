import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { currentLocation, setLocation } from "../test/navigation";
import { TestApp } from "../test/TestApp";

const ID = "11111111-1111-4111-8111-111111111111";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PROGRESS = {
  source_analysis: [],
  kpis: { status: "not_started" },
  dw_modeling: [
    { layer: "staging", status: "not_started" },
    { layer: "core", status: "not_started" },
    { layer: "mart", status: "not_started" },
  ],
};

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: role === "owner" ? ["workspace.edit", "workspace.view"] : ["workspace.view"],
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function open(role: WorkspaceRole, query = "") {
  setLocation(`/workspaces/${ID}${query}`);
  const handle = async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/v1/me") {
      return json(ADA);
    }
    if (path === `/api/v1/workspaces/${ID}`) {
      return json(workspace(role));
    }
    if (path === `/api/v1/workspaces/${ID}/progress`) {
      return json(PROGRESS);
    }
    if (path === `/api/v1/workspaces/${ID}/members`) {
      return json({ items: [] });
    }
    return json({ error: { code: "not_found", message: "Not Found", details: {} } }, 404);
  };
  const client = createApiClient({
    baseUrl: "http://dawam.test",
    fetch: async (input: RequestInfo | URL, init?: RequestInit) => handle(new Request(input, init)),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <TestApp />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
}

async function tree() {
  return screen.findByRole("tree", { name: "Workspace folders" });
}

function item(name: string) {
  return screen.getByRole("treeitem", { name });
}

function press(name: string, key: string) {
  fireEvent.keyDown(item(name), { key });
}

describe("the folder tree", () => {
  it("shows the spec's structure", async () => {
    open("owner");
    const root = await tree();

    const names = within(root)
      .getAllByRole("treeitem")
      .map((node) => node.getAttribute("aria-labelledby"))
      .map((id) => document.getElementById(id!)?.textContent);
    expect(names).toEqual([
      "Retail DW",
      "Systems",
      "Data Warehouse",
      "KPIs",
      "Staging",
      "Model",
      "Mappings",
      "Evaluation",
      "Core",
      "Model",
      "Mappings",
      "Evaluation",
      "Mart",
      "Model",
      "Mappings",
      "Evaluation",
      "Lineage",
      "Score",
      "DDL",
    ]);
    expect(item("Staging")).toHaveAttribute("aria-level", "3");
    expect(item("Staging")).toHaveAttribute("aria-expanded", "true");
    expect(item("KPIs")).not.toHaveAttribute("aria-expanded");
  });

  it("is the same tree for viewers and for owners", async () => {
    open("viewer");
    await tree();

    expect(screen.getAllByRole("treeitem")).toHaveLength(19);
    expect(screen.getByRole("heading", { name: "Retail DW" })).toBeInTheDocument();
  });

  it("starts with the Workspace selected and one tab stop on the tree", async () => {
    open("owner");
    await tree();

    expect(item("Retail DW")).toHaveAttribute("aria-selected", "true");
    const tabStops = screen.getAllByRole("treeitem").filter((n) => n.tabIndex === 0);
    expect(tabStops).toEqual([item("Retail DW")]);
  });

  it("moves focus with the arrow keys, Home and End", async () => {
    open("owner");
    await tree();
    item("Retail DW").focus();

    press("Retail DW", "ArrowDown");
    expect(item("Systems")).toHaveFocus();
    press("Systems", "ArrowDown");
    expect(item("Data Warehouse")).toHaveFocus();
    press("Data Warehouse", "ArrowDown");
    expect(item("KPIs")).toHaveFocus();
    press("KPIs", "ArrowUp");
    expect(item("Data Warehouse")).toHaveFocus();
    press("Data Warehouse", "End");
    expect(item("DDL")).toHaveFocus();
    press("DDL", "ArrowDown");
    expect(item("DDL")).toHaveFocus();
    press("DDL", "Home");
    expect(item("Retail DW")).toHaveFocus();
  });

  it("collapses and expands folders with Left and Right", async () => {
    open("owner");
    await tree();
    item("Data Warehouse").focus();

    press("Data Warehouse", "ArrowLeft");
    expect(item("Data Warehouse")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("treeitem", { name: "Staging" })).not.toBeInTheDocument();

    press("Data Warehouse", "ArrowRight");
    expect(item("Data Warehouse")).toHaveAttribute("aria-expanded", "true");
    expect(item("Data Warehouse")).toHaveFocus();
    press("Data Warehouse", "ArrowRight");
    expect(item("KPIs")).toHaveFocus();
  });

  it("keeps a tab stop and focus when a mouse collapse hides the selected folder", async () => {
    open("owner", "?folder=dw/staging/model");
    await tree();

    fireEvent.click(within(item("Data Warehouse")).getAllByText("▾")[0]!);

    expect(item("Data Warehouse")).toHaveAttribute("aria-expanded", "false");
    const tabStops = screen.getAllByRole("treeitem").filter((n) => n.tabIndex === 0);
    expect(tabStops).toEqual([item("Data Warehouse")]);
    expect(item("Data Warehouse")).toHaveFocus();
  });

  it("moves to the parent with Left from a leaf", async () => {
    open("owner");
    await tree();
    item("KPIs").focus();

    press("KPIs", "ArrowLeft");

    expect(item("Data Warehouse")).toHaveFocus();
  });

  it("selects the focused folder with Enter and puts it in the URL", async () => {
    open("owner");
    await tree();
    item("Retail DW").focus();

    press("Retail DW", "ArrowDown");
    expect(currentLocation()).toBe(`/workspaces/${ID}`);
    press("Systems", "Enter");

    expect(currentLocation()).toBe(`/workspaces/${ID}?folder=systems`);
    expect(item("Systems")).toHaveAttribute("aria-selected", "true");
    expect(item("Retail DW")).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("heading", { name: "Systems" })).toBeInTheDocument();
  });

  it("selects a folder on click", async () => {
    open("owner");
    await tree();

    // The second "Mappings" is the Core Layer's.
    fireEvent.click(screen.getAllByRole("treeitem", { name: "Mappings" })[1]!);

    expect(currentLocation()).toBe(`/workspaces/${ID}?folder=dw%2Fcore%2Fmappings`);
  });

  it("opens the folder named in the URL, so a link or a reload lands on it", async () => {
    open("viewer", "?folder=dw/staging/mappings");
    await tree();

    expect(screen.getByRole("heading", { name: "Mappings" })).toBeInTheDocument();
    const selected = screen
      .getAllByRole("treeitem")
      .filter((n) => n.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0]).toHaveAttribute("aria-level", "4");
    expect(screen.getByText("The Staging Layer's mappings will appear here once it exists.")).toBeInTheDocument();
  });

  it("falls back to the Workspace for an unknown folder", async () => {
    open("owner", "?folder=nonsense");
    await tree();

    expect(item("Retail DW")).toHaveAttribute("aria-selected", "true");
  });

  it("shows an empty state for folders whose features are not built yet", async () => {
    open("owner");
    await tree();

    fireEvent.click(item("Systems"));
    expect(
      screen.getByText("No Source Systems yet. Each Source System you add will get its own folder here."),
    ).toBeInTheDocument();
    fireEvent.click(item("Lineage"));
    expect(screen.getByText("Lineage appears here once there is a DW Schema to trace.")).toBeInTheDocument();
  });
});

describe("the stage-progress panel", () => {
  it("shows every stage as not started", async () => {
    open("viewer");

    const panel = await screen.findByRole("region", { name: "Stage progress" });

    const rows = (await within(panel).findAllByRole("listitem"))
      .map((row) => row.textContent);
    expect(rows).toEqual([
      "KPIsNot started",
      "DW Modeling: StagingNot started",
      "DW Modeling: CoreNot started",
      "DW Modeling: MartNot started",
    ]);
    expect(within(panel).getByText("No Source Systems yet, so there is no Source Analysis to show.")).toBeInTheDocument();
  });
});
