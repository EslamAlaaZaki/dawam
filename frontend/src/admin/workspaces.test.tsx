import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AdminWorkspace } from "../api/adminWorkspaces";
import type { Me } from "../api/queries";
import {
  CSRF_TOKEN,
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ADMIN: Me = {
  id: "00000000-0000-4000-8000-000000000001",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};

const GRACE = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "grace@example.com",
  display_name: "Grace Hopper",
  system_role: "user",
  is_active: true,
  must_change_password: false,
  created_at: "2026-01-05T09:00:00Z",
  last_login_at: null,
};

function workspace(name: string, more: Partial<AdminWorkspace> = {}): AdminWorkspace {
  return {
    id: `${name.length}`.padStart(8, "0") + "-1111-4111-8111-111111111111",
    name,
    status: "active",
    owners: [
      {
        user_id: "33333333-3333-4333-8333-333333333333",
        email: "ada@example.com",
        display_name: "Ada Lovelace",
        is_active: true,
      },
    ],
    member_count: 3,
    has_active_owner: true,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-06T09:00:00Z",
    archived_at: null,
    ...more,
  };
}

const ORPHAN = workspace("Orphaned DW", {
  has_active_owner: false,
  owners: [
    {
      user_id: "44444444-4444-4444-8444-444444444444",
      email: "gone@example.com",
      display_name: "Gone Owner",
      is_active: false,
    },
  ],
});

function backend(handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADMIN);
      case "GET /api/v1/admin/workspaces":
        return json({ items: [workspace("Retail DW"), ORPHAN], next_cursor: null });
      case "GET /api/v1/admin/users":
        return json({ items: [GRACE], next_cursor: null });
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

describe("the admin Workspace list", () => {
  it("shows every Workspace's name, owners, member count and dates", async () => {
    renderApp(backend(), "/admin/workspaces");

    const retail = await screen.findByRole("row", { name: "Retail DW" });

    expect(retail).toHaveTextContent("Ada Lovelace (ada@example.com)");
    expect(retail).toHaveTextContent("Active");
    expect(retail).toHaveTextContent("3");
    expect(within(retail).queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByRole("row", { name: "Orphaned DW" })).toHaveTextContent("deactivated");
  });

  it("offers reassignment only for a Workspace without an active owner", async () => {
    const requests = renderApp(
      backend((r) =>
        r.method === "POST" && r.path.endsWith("/reassign-owner")
          ? json({ ...ORPHAN, has_active_owner: true })
          : undefined,
      ),
      "/admin/workspaces",
    );

    const orphan = await screen.findByRole("row", { name: "Orphaned DW" });
    fireEvent.click(within(orphan).getByRole("button", { name: "Reassign ownership" }));
    const form = await screen.findByRole("form", { name: "Reassign Orphaned DW" });
    fireEvent.change(await within(form).findByLabelText("New owner"), {
      target: { value: GRACE.id },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Make owner" }));

    await screen.findByRole("row", { name: "Orphaned DW" });
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: `/api/v1/admin/workspaces/${ORPHAN.id}/reassign-owner`,
      csrf: CSRF_TOKEN,
      body: { user_id: GRACE.id },
    });
  });

  it("shows why a reassignment is refused", async () => {
    renderApp(
      backend((r) =>
        r.method === "POST"
          ? apiError(409, "has_active_owner", "This Workspace still has an active owner.")
          : undefined,
      ),
      "/admin/workspaces",
    );

    const orphan = await screen.findByRole("row", { name: "Orphaned DW" });
    fireEvent.click(within(orphan).getByRole("button", { name: "Reassign ownership" }));
    const form = await screen.findByRole("form", { name: "Reassign Orphaned DW" });
    fireEvent.change(await within(form).findByLabelText("New owner"), {
      target: { value: GRACE.id },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Make owner" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent("still has an active owner");
  });
});
