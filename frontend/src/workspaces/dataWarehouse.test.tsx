import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import type { DataWarehouse } from "../api/dataWarehouse";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { setLocation } from "../test/navigation";
import { TestApp } from "../test/TestApp";

const ID = "11111111-1111-4111-8111-111111111111";
const CSRF_TOKEN = "csrf-token-from-the-cookie";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PROGRESS = { source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] };

const NOT_SET_UP: DataWarehouse = {
  set_up: false,
  id: null,
  target_platform: null,
  layer_schemas: null,
  naming_rules: null,
  date_dimension: null,
  set_up_at: null,
  updated_at: null,
  version: null,
};

const SET_UP: DataWarehouse = {
  set_up: true,
  id: "22222222-2222-4222-8222-222222222222",
  target_platform: "oracle",
  layer_schemas: { staging: "stg", core: "cr", mart: "mt" },
  naming_rules: {
    case_style: "upper",
    dimension_prefix: "d_",
    fact_prefix: "f_",
    bridge_prefix: "b_",
    load_ts_column: "load_ts",
    source_system_column: "source_system",
  },
  date_dimension: {
    start_year: 2010,
    end_year: 2030,
    weekend_days: ["friday", "saturday"],
    include_hijri: true,
    fiscal_year_start_month: 7,
    include_time_dimension: false,
  },
  set_up_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
  version: 3,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: ["data_warehouse.change_platform", "data_warehouse.set_up", "workspace.view"],
  editor: ["data_warehouse.set_up", "workspace.view"],
  viewer: ["workspace.view"],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Sent {
  method: string;
  body: Record<string, unknown>;
}

function open(
  role: WorkspaceRole,
  warehouse: DataWarehouse | "error",
  folder = "dw",
  sent: Sent[] = [],
) {
  let current = warehouse;
  setLocation(`/workspaces/${ID}?folder=${folder}`);
  const handle = async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/v1/me") {
      return json(ADA);
    }
    if (path === `/api/v1/workspaces/${ID}`) {
      return json({
        id: ID,
        name: "Retail DW",
        description: "",
        domain: "",
        role,
        permissions: PERMISSIONS[role],
        version: 1,
        created_at: "2026-01-05T09:00:00Z",
        updated_at: "2026-01-05T09:00:00Z",
      });
    }
    if (path === `/api/v1/workspaces/${ID}/progress`) {
      return json(PROGRESS);
    }
    if (path === `/api/v1/workspaces/${ID}/data-warehouse`) {
      if (request.method === "GET") {
        return current === "error"
          ? json({ error: { code: "boom", message: "Boom", details: {} } }, 500)
          : json(current);
      }
      sent.push({ method: request.method, body: await request.json() });
      current = SET_UP;
      return json(SET_UP);
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

beforeEach(() => {
  document.cookie = `dawam_csrf=${CSRF_TOKEN}; path=/`;
});

afterEach(() => {
  document.cookie = "dawam_csrf=; path=/; max-age=0";
});

function names() {
  return screen.getAllByRole("treeitem").map((node) => {
    const id = node.getAttribute("aria-labelledby");
    return document.getElementById(id!)?.textContent;
  });
}

describe("the Data Warehouse folder before setup", () => {
  it("shows only the setup step and KPIs", async () => {
    open("editor", NOT_SET_UP);
    await screen.findByRole("tree", { name: "Workspace folders" });

    expect(names()).toEqual(["Retail DW", "Systems", "Data Warehouse", "Set up Data Warehouse", "KPIs"]);
    expect(await screen.findByRole("heading", { name: "Set up Data Warehouse" })).toBeInTheDocument();
  });

  it("lets an editor choose the platform and sets it up", async () => {
    const sent: Sent[] = [];
    open("editor", NOT_SET_UP, "dw/setup", sent);

    fireEvent.change(await screen.findByLabelText("Target platform"), {
      target: { value: "snowflake" },
    });
    fireEvent.change(screen.getByLabelText("Schema or dataset name: staging"), {
      target: { value: "raw" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set up Data Warehouse" }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.method).toBe("POST");
    expect(sent[0]!.body).toMatchObject({
      target_platform: "snowflake",
      layer_schemas: { staging: "raw", core: "core", mart: "mart" },
      naming_rules: { case_style: "lower", dimension_prefix: "dim_" },
      date_dimension: { weekend_days: ["saturday", "sunday"], fiscal_year_start_month: null },
    });
  });

  it("is read-only for a viewer", async () => {
    open("viewer", NOT_SET_UP);

    expect(await screen.findByLabelText("Target platform")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Set up Data Warehouse" })).not.toBeInTheDocument();
  });
});

describe("the Data Warehouse folder after setup", () => {
  it("shows the full folder", async () => {
    open("owner", SET_UP);
    await screen.findByRole("tree", { name: "Workspace folders" });

    await waitFor(() => expect(names()).toContain("Staging"));
    expect(names()).not.toContain("Set up Data Warehouse");
  });

  it("keeps the platform to owners", async () => {
    open("editor", SET_UP);

    const platform = await screen.findByLabelText("Target platform");
    expect(platform).toBeDisabled();
    expect(platform).toHaveValue("oracle");
    expect(screen.getByText("Only owners can change the platform after setup.")).toBeInTheDocument();
  });

  it("lets an owner change the platform, sending the version", async () => {
    const sent: Sent[] = [];
    open("owner", SET_UP, "dw", sent);

    fireEvent.change(await screen.findByLabelText("Target platform"), {
      target: { value: "bigquery" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.method).toBe("PATCH");
    expect(sent[0]!.body).toMatchObject({ version: 3, target_platform: "bigquery" });
  });

  it("lets an editor save other settings without a platform", async () => {
    const sent: Sent[] = [];
    open("editor", SET_UP, "dw", sent);

    fireEvent.change(await screen.findByLabelText("Fact prefix"), { target: { value: "fct_" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]!.body).toMatchObject({ version: 3, naming_rules: { fact_prefix: "fct_" } });
    expect(sent[0]!.body).not.toHaveProperty("target_platform");
  });

  it("stays in the Data Warehouse folder after the setup succeeds", async () => {
    open("editor", NOT_SET_UP, "dw/setup");

    fireEvent.click(await screen.findByRole("button", { name: "Set up Data Warehouse" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(screen.getByRole("heading", { name: "Data Warehouse setup" })).toBeInTheDocument();
    expect(names()).toContain("Staging");
    expect(names()).not.toContain("Set up Data Warehouse");
  });
});

describe("a link into the Data Warehouse folder", () => {
  it("waits for the setup to load instead of falling back to the Workspace", async () => {
    open("owner", SET_UP, "dw/staging");

    expect(await screen.findByRole("heading", { name: "Staging" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Details" })).not.toBeInTheDocument();
  });

  it("shows an error, not the Workspace, when the setup cannot be loaded", async () => {
    open("owner", "error", "dw/staging");

    expect(await screen.findByText(/Could not load the Data Warehouse/)).toHaveAttribute(
      "role",
      "alert",
    );
    expect(screen.queryByRole("heading", { name: "Details" })).not.toBeInTheDocument();
  });
});
