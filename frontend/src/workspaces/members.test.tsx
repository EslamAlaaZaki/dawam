import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Member } from "../api/members";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import { currentLocation } from "../test/navigation";
import {
  apiError,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const MEMBERS = `/api/v1/workspaces/${WORKSPACE_ID}/members`;

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

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: PERMISSIONS[role],
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

function member(user_id: string, display_name: string, role: WorkspaceRole): Member {
  return {
    user_id,
    email: `${(display_name.split(" ")[0] ?? "").toLowerCase()}@example.com`,
    display_name,
    role,
    is_active: true,
    added_at: "2026-01-05T09:00:00Z",
  };
}

const ADA_MEMBER = member(ADA.id, "Ada Lovelace", "owner");
const GRACE = member("22222222-2222-4222-8222-222222222222", "Grace Hopper", "viewer");

/** Ada, as `role`, opens the Workspace; `handle` answers everything else. */
function backend(
  role: WorkspaceRole,
  members: Member[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case `GET ${MEMBERS}`:
        return json({ items: members });
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

describe("the members panel", () => {
  it("lists the members with their roles for any member, read-only for non-owners", async () => {
    renderApp(backend("viewer", [{ ...ADA_MEMBER, role: "viewer" }, { ...GRACE, role: "owner" }]), PATH);

    const grace = await screen.findByRole("row", { name: "Grace Hopper" });
    expect(grace).toHaveTextContent("grace@example.com");
    expect(grace).toHaveTextContent("Owner");
    expect(screen.getByRole("row", { name: "Ada Lovelace" })).toHaveTextContent("(you)");
    expect(screen.queryByRole("form", { name: "Add member" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /Role of/ })).not.toBeInTheDocument();
  });

  it("lets an owner add someone by email with a role", async () => {
    const members = [ADA_MEMBER];
    const requests = renderApp(
      backend("owner", members, (r) => {
        if (r.method === "POST" && r.path === MEMBERS) {
          const added = member("33333333-3333-4333-8333-333333333333", "Alan Turing", "viewer");
          members.push(added);
          return json({ outcome: "added", member: added }, 201);
        }
      }),
      PATH,
    );

    const form = await screen.findByRole("form", { name: "Add member" });
    fireEvent.change(within(form).getByLabelText("Email"), {
      target: { value: "alan@example.com" },
    });
    fireEvent.change(within(form).getByLabelText("Role"), { target: { value: "viewer" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add member" }));

    expect(await screen.findByText("Alan Turing is now a viewer.")).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: "Alan Turing" })).toBeInTheDocument();
    expect(requests.find((r) => r.method === "POST")?.body).toEqual({
      email: "alan@example.com",
      role: "viewer",
    });
  });

  it("tells the owner when someone was invited instead", async () => {
    renderApp(
      backend("owner", [ADA_MEMBER], (r) =>
        r.method === "POST" && r.path === MEMBERS
          ? json(
              {
                outcome: "invited",
                invitation: { email: "new@example.com", expires_at: "2026-01-12T09:00:00Z" },
                delivery: "sent",
              },
              201,
            )
          : undefined,
      ),
      PATH,
    );

    const form = await screen.findByRole("form", { name: "Add member" });
    fireEvent.change(within(form).getByLabelText("Email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add member" }));

    expect(await screen.findByText("Invitation emailed to new@example.com.")).toBeInTheDocument();
  });

  it("shows why an invitation is refused", async () => {
    renderApp(
      backend("owner", [ADA_MEMBER], (r) =>
        r.method === "POST" && r.path === MEMBERS
          ? apiError(403, "invite_not_allowed", "Only an admin can invite new people.")
          : undefined,
      ),
      PATH,
    );

    const form = await screen.findByRole("form", { name: "Add member" });
    fireEvent.change(within(form).getByLabelText("Email"), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Add member" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Only an admin can invite new people.",
    );
  });

  it("lets an owner change a member's role", async () => {
    const requests = renderApp(
      backend("owner", [ADA_MEMBER, GRACE], (r) =>
        r.method === "PATCH" ? json({ ...GRACE, role: "editor" }) : undefined,
      ),
      PATH,
    );

    fireEvent.change(await screen.findByRole("combobox", { name: "Role of Grace Hopper" }), {
      target: { value: "editor" },
    });

    await waitFor(() =>
      expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
        path: `${MEMBERS}/${GRACE.user_id}`,
        body: { role: "editor" },
      }),
    );
  });

  it("shows the last-owner refusal", async () => {
    renderApp(
      backend("owner", [ADA_MEMBER, GRACE], (r) =>
        r.method === "PATCH"
          ? apiError(409, "last_owner", "A Workspace needs at least one owner.")
          : undefined,
      ),
      PATH,
    );

    fireEvent.change(await screen.findByRole("combobox", { name: "Role of Ada Lovelace" }), {
      target: { value: "viewer" },
    });

    const row = screen.getByRole("row", { name: "Ada Lovelace" });
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "A Workspace needs at least one owner.",
    );
  });

  it("removes a member after confirming", async () => {
    const members = [ADA_MEMBER, GRACE];
    const requests = renderApp(
      backend("owner", members, (r) => {
        if (r.method === "DELETE") {
          members.pop();
          return noContent();
        }
      }),
      PATH,
    );

    const grace = await screen.findByRole("row", { name: "Grace Hopper" });
    fireEvent.click(within(grace).getByRole("button", { name: "Remove" }));
    expect(requests.some((r) => r.method === "DELETE")).toBe(false);
    fireEvent.click(within(grace).getByRole("button", { name: "Remove Grace Hopper" }));

    await waitFor(() =>
      expect(screen.queryByRole("row", { name: "Grace Hopper" })).not.toBeInTheDocument(),
    );
    expect(requests.find((r) => r.method === "DELETE")?.path).toBe(`${MEMBERS}/${GRACE.user_id}`);
  });

  it("transfers ownership after confirming", async () => {
    const requests = renderApp(
      backend("owner", [ADA_MEMBER, GRACE], (r) =>
        r.method === "POST" && r.path.endsWith("/transfer-ownership")
          ? json(workspace("editor"))
          : undefined,
      ),
      PATH,
    );

    const grace = await screen.findByRole("row", { name: "Grace Hopper" });
    fireEvent.click(within(grace).getByRole("button", { name: "Transfer ownership" }));
    fireEvent.click(
      within(grace).getByRole("button", {
        name: "Make Grace Hopper owner, and become an editor",
      }),
    );

    await waitFor(() =>
      expect(requests.find((r) => r.path.endsWith("/transfer-ownership"))?.body).toEqual({
        user_id: GRACE.user_id,
      }),
    );
  });

  it("lets a member leave and goes back to the Workspace list", async () => {
    renderApp(
      backend("viewer", [{ ...ADA_MEMBER, role: "viewer" }, GRACE], (r) =>
        r.method === "POST" && r.path.endsWith("/leave") ? noContent() : undefined,
      ),
      PATH,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Leave Workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Leave this Workspace" }));

    await waitFor(() => expect(currentLocation()).toBe("/"));
  });
});
