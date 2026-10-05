import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { Workspace } from "../api/workspaces";
import { currentLocation } from "../test/navigation";
import {
  CSRF_TOKEN,
  apiError,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const ACTIVE_OWNER: Workspace["permissions"] = [
  "workspace.archive",
  "workspace.delete",
  "workspace.edit",
  "workspace.leave",
  "workspace.manage_members",
  "workspace.transfer_ownership",
  "workspace.view",
];
const ARCHIVED_OWNER: Workspace["permissions"] = [
  "workspace.delete",
  "workspace.leave",
  "workspace.unarchive",
  "workspace.view",
];

function workspace(state: { archived: boolean; permissions: Workspace["permissions"] }): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role: "owner",
    permissions: state.permissions,
    status: state.archived ? "archived" : "active",
    archived_at: state.archived ? "2026-01-06T09:00:00Z" : null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

/** Ada owns the Workspace; `archived` flips as the API is called. */
function backend(initial: { archived: boolean; permissions?: Workspace["permissions"] }) {
  const state = { archived: initial.archived };
  const handle = (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(
          workspace({
            archived: state.archived,
            permissions:
              initial.permissions ?? (state.archived ? ARCHIVED_OWNER : ACTIVE_OWNER),
          }),
        );
      case `GET ${API}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET ${API}/members`:
        return json({ items: [] });
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case `POST ${API}/archive`:
        state.archived = true;
        return noContent();
      case `POST ${API}/unarchive`:
        state.archived = false;
        return noContent();
      case `DELETE ${API}`:
        return (r.body as { name: string }).name === "Retail DW"
          ? noContent()
          : apiError(422, "name_mismatch", "Type the Workspace's name exactly to delete it.");
    }
  };
  return handle;
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("archiving a Workspace", () => {
  it("lets an owner archive it, after which it says it is read-only", async () => {
    const requests = renderApp(backend({ archived: false }), PATH);

    fireEvent.click(await screen.findByRole("button", { name: "Archive Workspace" }));

    expect(await screen.findByRole("status")).toHaveTextContent("archived and read-only");
    expect(requests.find((r) => r.method === "POST" && r.path === `${API}/archive`)).toMatchObject(
      { csrf: CSRF_TOKEN },
    );
    // Read-only: no edit form, and unarchiving is on offer instead.
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unarchive Workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive Workspace" })).not.toBeInTheDocument();
  });

  it("lets an owner unarchive it", async () => {
    renderApp(backend({ archived: true }), PATH);

    fireEvent.click(await screen.findByRole("button", { name: "Unarchive Workspace" }));

    expect(await screen.findByRole("button", { name: "Archive Workspace" })).toBeInTheDocument();
    expect(screen.queryByText(/archived and read-only/)).not.toBeInTheDocument();
  });

  it("offers neither to a member the API gives no such permission", async () => {
    renderApp(backend({ archived: false, permissions: ["workspace.view"] }), PATH);

    await screen.findByRole("heading", { name: "Retail DW" });

    expect(screen.queryByRole("button", { name: /rchive Workspace/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Delete Workspace" })).not.toBeInTheDocument();
  });
});

describe("deleting a Workspace", () => {
  it("needs the name typed, then deletes it and goes home", async () => {
    const requests = renderApp(backend({ archived: false }), PATH);
    const form = await screen.findByRole("form", { name: "Delete Workspace" });
    const submit = screen.getByRole("button", { name: "Delete Workspace" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Workspace name"), { target: { value: "Retail" } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Workspace name"), { target: { value: "Retail DW" } });
    expect(submit).toBeEnabled();
    fireEvent.submit(form);

    await waitFor(() => expect(currentLocation()).toBe("/"));
    expect(requests.find((r) => r.method === "DELETE")).toMatchObject({
      path: API,
      csrf: CSRF_TOKEN,
      body: { name: "Retail DW" },
    });
  });
});
