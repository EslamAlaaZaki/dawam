import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { PendingInvitation } from "../api/invitations";
import type { Me } from "../api/queries";
import {
  CSRF_TOKEN,
  apiError,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ROOT: Me = {
  id: "0b6c1f0e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};

const AMY: PendingInvitation = {
  id: "6a1d0c2e-0000-4000-8000-000000000001",
  email: "amy@example.com",
  invited_by: { id: ROOT.id, display_name: "Root Admin" },
  workspace_id: null,
  workspace_role: null,
  created_at: "2026-01-05T09:00:00Z",
  expires_at: "2026-01-12T09:00:00Z",
};

function as(me: Me, handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  return (request: ApiRequest) => (request.path === "/api/v1/me" ? json(me) : handle(request));
}

function page(items: PendingInvitation[], next_cursor: string | null = null) {
  return json({ items, next_cursor });
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the invitations page", () => {
  it("is linked from the user list and the admin navigation", async () => {
    renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/users" ? json({ items: [], next_cursor: null }) : undefined)),
      "/admin/users",
    );

    expect(await screen.findByRole("link", { name: "Invite user" })).toHaveAttribute(
      "href",
      "/admin/invitations",
    );
    const nav = await screen.findByRole("navigation", { name: "Administration" });
    expect(within(nav).getByRole("link", { name: "Invitations" })).toHaveAttribute(
      "href",
      "/admin/invitations",
    );
  });

  it("lists pending invitations", async () => {
    renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/invitations" ? page([AMY]) : undefined)),
      "/admin/invitations",
    );

    expect(await screen.findByRole("heading", { name: "Invitations" })).toBeInTheDocument();
    const amy = await screen.findByRole("row", { name: /amy@example\.com/ });
    expect(amy).toHaveTextContent("Root Admin");
  });

  it("says when nobody is invited", async () => {
    renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/invitations" ? page([]) : undefined)),
      "/admin/invitations",
    );

    expect(await screen.findByText("No pending invitations.")).toBeInTheDocument();
  });
});

describe("inviting someone", () => {
  function inviting(onInvite: (r: ApiRequest) => Response) {
    let pending: PendingInvitation[] = [];
    return as(ROOT, (r) => {
      if (r.method === "GET" && r.path === "/api/v1/admin/invitations") return page(pending);
      if (r.method === "POST" && r.path === "/api/v1/admin/users/invite") {
        const response = onInvite(r);
        if (response.ok) pending = [{ ...AMY, email: (r.body as { email: string }).email }];
        return response;
      }
      return undefined;
    });
  }

  function invite(email: string) {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
    fireEvent.click(screen.getByRole("button", { name: "Send invitation" }));
  }

  it("sends the invitation and lists it as pending", async () => {
    const requests = renderApp(
      inviting(() => json({ invitation: AMY, delivery: "sent" }, 201)),
      "/admin/invitations",
    );
    await screen.findByText("No pending invitations.");

    invite("amy@example.com");

    expect(await screen.findByText("Invitation emailed to amy@example.com.")).toBeInTheDocument();
    expect(await screen.findByRole("row", { name: /amy@example\.com/ })).toBeInTheDocument();
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/v1/admin/users/invite",
      query: {},
      csrf: CSRF_TOKEN,
      body: { email: "amy@example.com" },
    });
  });

  it("points to the link to share when it could not be emailed", async () => {
    renderApp(
      inviting(() => json({ invitation: AMY, delivery: "link_for_admin" }, 201)),
      "/admin/invitations",
    );
    await screen.findByText("No pending invitations.");

    invite("amy@example.com");

    const note = await screen.findByText(/could not be emailed/);
    expect(within(note).getByRole("link", { name: "Links to share" })).toHaveAttribute(
      "href",
      "/admin/email/links",
    );
  });

  it("shows why an invitation was refused", async () => {
    renderApp(
      inviting(() =>
        apiError(409, "email_taken", "grace@example.com already has an account: they can sign in."),
      ),
      "/admin/invitations",
    );
    await screen.findByText("No pending invitations.");

    invite("grace@example.com");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "grace@example.com already has an account",
    );
  });
});

describe("revoking an invitation", () => {
  it("revokes it and drops it from the list", async () => {
    let pending = [AMY];
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.method === "GET" && r.path === "/api/v1/admin/invitations") return page(pending);
        if (r.method === "DELETE" && r.path === `/api/v1/admin/invitations/${AMY.id}`) {
          pending = [];
          return noContent();
        }
        return undefined;
      }),
      "/admin/invitations",
    );
    const amy = await screen.findByRole("row", { name: /amy@example\.com/ });

    fireEvent.click(within(amy).getByRole("button", { name: "Revoke" }));

    expect(await screen.findByText("No pending invitations.")).toBeInTheDocument();
    await waitFor(() =>
      expect(requests).toContainEqual(
        expect.objectContaining({
          method: "DELETE",
          path: `/api/v1/admin/invitations/${AMY.id}`,
          csrf: CSRF_TOKEN,
        }),
      ),
    );
  });
});
