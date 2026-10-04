import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { AdminUser } from "../api/users";
import type { Me } from "../api/queries";
import {
  CSRF_TOKEN,
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";
import { currentLocation } from "../test/navigation";

const ROOT: Me = {
  id: "0b6c1f0e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};

const GRACE: AdminUser = {
  id: "1c2d3e4f-0000-4000-8000-000000000001",
  email: "grace@example.com",
  display_name: "Grace Hopper",
  system_role: "user",
  is_active: true,
  must_change_password: false,
  created_at: "2026-01-05T09:00:00Z",
  last_login_at: null,
};

const ROOT_ROW: AdminUser = {
  ...GRACE,
  id: ROOT.id,
  email: ROOT.email,
  display_name: ROOT.display_name,
  system_role: "admin",
};

function as(me: Me, handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  return (request: ApiRequest) => (request.path === "/api/v1/me" ? json(me) : handle(request));
}

function page(items: AdminUser[], next_cursor: string | null = null) {
  return json({ items, next_cursor });
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the users page", () => {
  it("is linked from the admin navigation and lists every user", async () => {
    renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/users" ? page([GRACE, ROOT_ROW]) : undefined)),
      "/admin/users",
    );

    const nav = await screen.findByRole("navigation", { name: "Administration" });
    expect(within(nav).getByRole("link", { name: "Users" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    const grace = await screen.findByRole("row", { name: /grace@example\.com/ });
    expect(grace).toHaveTextContent("Grace Hopper");
    expect(grace).toHaveTextContent("User");
    expect(grace).toHaveTextContent("Active");
    expect(screen.getByRole("row", { name: /root@example\.com/ })).toHaveTextContent("Admin");
  });
});

describe("searching and paging users", () => {
  it("sends the search text, role and status to the API", async () => {
    const requests = renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/users" ? page([GRACE]) : undefined)),
      "/admin/users",
    );
    await screen.findByRole("row", { name: /grace@example\.com/ });

    fireEvent.change(screen.getByLabelText("Search"), { target: { value: " grace " } });
    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "user" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "deactivated" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() =>
      expect(requests.filter((r) => r.path === "/api/v1/admin/users").at(-1)?.query).toEqual({
        q: "grace",
        role: "user",
        active: "false",
      }),
    );
  });

  it("loads the next page with the cursor", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path !== "/api/v1/admin/users") return undefined;
        return r.query.cursor === "after-grace" ? page([ROOT_ROW]) : page([GRACE], "after-grace");
      }),
      "/admin/users",
    );
    await screen.findByRole("row", { name: /grace@example\.com/ });

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("row", { name: /root@example\.com/ })).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /grace@example\.com/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(requests.filter((r) => r.path === "/api/v1/admin/users").at(-1)?.query).toEqual({
      cursor: "after-grace",
    });
  });
});

describe("changing a user", () => {
  function withGrace(onPatch: (r: ApiRequest) => Response) {
    let grace = { ...GRACE };
    return as(ROOT, (r) => {
      if (r.method === "GET" && r.path === "/api/v1/admin/users") return page([grace, ROOT_ROW]);
      if (r.method === "PATCH") {
        const response = onPatch(r);
        if (response.ok) grace = { ...grace, ...(r.body as object) };
        return response;
      }
      if (r.method === "POST" && r.path === `/api/v1/admin/users/${GRACE.id}/force-reset`) {
        return json({ delivery: "link_for_admin" });
      }
      return undefined;
    });
  }

  it("deactivates a user, then shows them as deactivated", async () => {
    const requests = renderApp(
      withGrace((r) => json({ ...GRACE, ...(r.body as object) })),
      "/admin/users",
    );
    const row = await screen.findByRole("row", { name: /grace@example\.com/ });

    fireEvent.click(within(row).getByRole("button", { name: "Deactivate" }));

    await waitFor(() =>
      expect(screen.getByRole("row", { name: /grace@example\.com/ })).toHaveTextContent(
        "Deactivated",
      ),
    );
    expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
      path: `/api/v1/admin/users/${GRACE.id}`,
      csrf: CSRF_TOKEN,
      body: { is_active: false },
    });
  });

  it("promotes a user to admin", async () => {
    const requests = renderApp(
      withGrace((r) => json({ ...GRACE, ...(r.body as object) })),
      "/admin/users",
    );
    const row = await screen.findByRole("row", { name: /grace@example\.com/ });

    fireEvent.click(within(row).getByRole("button", { name: "Make admin" }));

    await waitFor(() =>
      expect(
        within(screen.getByRole("row", { name: /grace@example\.com/ })).getByRole("button", {
          name: "Make user",
        }),
      ).toBeInTheDocument(),
    );
    expect(requests.find((r) => r.method === "PATCH")?.body).toEqual({ system_role: "admin" });
  });

  it("shows why the last admin cannot be demoted", async () => {
    renderApp(
      withGrace(() =>
        apiError(409, "last_admin", "This is the last active admin; promote another user first."),
      ),
      "/admin/users",
    );
    const row = await screen.findByRole("row", { name: /root@example\.com/ });

    fireEvent.click(within(row).getByRole("button", { name: "Make user" }));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "This is the last active admin; promote another user first.",
    );
  });

  it("forces a password reset and points to the link when it could not be emailed", async () => {
    renderApp(withGrace(() => json(GRACE)), "/admin/users");
    const row = await screen.findByRole("row", { name: /grace@example\.com/ });

    fireEvent.click(within(row).getByRole("button", { name: "Force password reset" }));

    expect(await within(row).findByText(/could not be emailed/)).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Links to share" })).toHaveAttribute(
      "href",
      "/admin/email/links",
    );
  });
});

describe("creating a user", () => {
  it("creates a user with a temporary password and goes back to the list", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.method === "POST" && r.path === "/api/v1/admin/users") {
          return json({ ...GRACE, must_change_password: true }, 201);
        }
        if (r.path === "/api/v1/admin/users") return page([GRACE]);
        return undefined;
      }),
      "/admin/users",
    );
    fireEvent.click(await screen.findByRole("link", { name: "Create user" }));
    expect(await screen.findByRole("heading", { name: "Create user" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "grace@example.com" } });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Grace Hopper" } });
    fireEvent.change(screen.getByLabelText("System role"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), {
      target: { value: "temporary password 1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));

    await waitFor(() => expect(currentLocation()).toBe("/admin/users"));
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: "/api/v1/admin/users",
      csrf: CSRF_TOKEN,
      body: {
        email: "grace@example.com",
        display_name: "Grace Hopper",
        system_role: "admin",
        temporary_password: "temporary password 1",
      },
    });
  });

  it("shows the API's message when the temporary password is too weak", async () => {
    renderApp(
      as(ROOT, (r) =>
        r.method === "POST" && r.path === "/api/v1/admin/users"
          ? apiError(422, "invalid_password", "The password must be at least 10 characters long.")
          : undefined,
      ),
      "/admin/users/new",
    );

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "grace@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Grace" } });
    fireEvent.change(screen.getByLabelText("Temporary password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Create user" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The password must be at least 10 characters long.",
    );
    expect(currentLocation()).toBe("/admin/users/new");
  });
});

const EVENT = {
  id: "9e8d7c6b-0000-4000-8000-000000000001",
  event_type: "user_deactivated",
  actor_id: ROOT.id,
  actor_email: ROOT.email,
  target_type: "user",
  target_id: GRACE.id,
  metadata: { email: GRACE.email, sessions_ended: 1 },
  ip: "203.0.113.7",
  created_at: "2026-01-05T09:00:00Z",
};

describe("the security events page", () => {
  it("is linked from the admin navigation and lists events", async () => {
    renderApp(
      as(ROOT, (r) =>
        r.path === "/api/v1/admin/security-events"
          ? json({ items: [EVENT], next_cursor: null })
          : undefined,
      ),
      "/admin/security-events",
    );

    const nav = await screen.findByRole("navigation", { name: "Administration" });
    expect(within(nav).getByRole("link", { name: "Security events" })).toHaveAttribute(
      "href",
      "/admin/security-events",
    );
    expect(await screen.findByRole("heading", { name: "Security events" })).toBeInTheDocument();
    const row = await screen.findByRole("row", { name: /user_deactivated/ });
    expect(row).toHaveTextContent("root@example.com");
    expect(row).toHaveTextContent("grace@example.com");
    expect(row).toHaveTextContent("203.0.113.7");
  });

  it("filters by event type, actor and date", async () => {
    const requests = renderApp(
      as(ROOT, (r) =>
        r.path === "/api/v1/admin/security-events"
          ? json({ items: [EVENT], next_cursor: null })
          : undefined,
      ),
      "/admin/security-events",
    );
    await screen.findByRole("row", { name: /user_deactivated/ });

    fireEvent.change(screen.getByLabelText("Event type"), {
      target: { value: "user_deactivated" },
    });
    fireEvent.change(screen.getByLabelText("Actor email"), {
      target: { value: "root@example.com" },
    });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-05" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-01-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Filter" }));

    await waitFor(() => {
      const query = requests
        .filter((r) => r.path === "/api/v1/admin/security-events")
        .at(-1)?.query;
      expect(query).toMatchObject({ event_type: "user_deactivated", actor: "root@example.com" });
      // Whole local days: from the start of "From" to the end of "To".
      expect(new Date(query!.since!)).toEqual(new Date(2026, 0, 5));
      expect(new Date(query!.until!)).toEqual(new Date(2026, 0, 7));
    });
  });
});

describe("a user with a temporary password", () => {
  const FLAGGED: Me = {
    ...ROOT,
    email: "grace@example.com",
    display_name: "Grace",
    system_role: "user",
    must_change_password: true,
  };

  it("is sent to the change-password page and kept there", async () => {
    renderApp(as(FLAGGED), "/");

    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/change-password");
  });

  it("is let through once the password is changed", async () => {
    let me = FLAGGED;
    const requests = renderApp(
      (r) => {
        if (r.path === "/api/v1/me") return json(me);
        if (r.path === "/api/v1/auth/password/change") {
          me = { ...me, must_change_password: false };
          return new Response(null, { status: 204 });
        }
        if (r.path === "/api/v1/workspaces") return json({ items: [], next_cursor: null });
        return undefined;
      },
      "/change-password",
    );

    fireEvent.change(await screen.findByLabelText("Temporary password"), {
      target: { value: "temporary password 1" },
    });
    fireEvent.change(screen.getByLabelText("New password"), {
      target: { value: "my very own password" },
    });
    fireEvent.change(screen.getByLabelText("Confirm new password"), {
      target: { value: "my very own password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() => expect(currentLocation()).toBe("/"));
    expect(requests.find((r) => r.path === "/api/v1/auth/password/change")?.body).toEqual({
      current_password: "temporary password 1",
      new_password: "my very own password",
    });
  });
});
