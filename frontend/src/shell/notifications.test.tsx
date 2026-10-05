import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Notification } from "../api/notifications";
import type { Me } from "../api/queries";
import {
  CSRF_TOKEN,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function note(id: string, message: string): Notification {
  return {
    id,
    kind: "ownership",
    message,
    workspace_id: "11111111-1111-4111-8111-111111111111",
    ref_type: "workspace",
    ref_id: "11111111-1111-4111-8111-111111111111",
    created_at: "2026-01-05T09:00:00Z",
  };
}

const FIRST = note("a0000000-0000-4000-8000-000000000001", "Grace is the new owner of Retail DW.");
const SECOND = note("a0000000-0000-4000-8000-000000000002", "Alan is the new owner of Sales DW.");

function backend(unread: Notification[]) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case "GET /api/v1/notifications":
        return json({ items: unread, unread_count: unread.length });
      case "POST /api/v1/notifications/read-all":
        unread.splice(0);
        return noContent();
    }
    const read = /^\/api\/v1\/notifications\/([^/]+)\/read$/.exec(r.path);
    if (r.method === "POST" && read) {
      unread.splice(
        unread.findIndex((n) => n.id === read[1]),
        1,
      );
      return noContent();
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the notification bell in the header", () => {
  it("shows the unread count and lists the notifications when opened", async () => {
    renderApp(backend([FIRST, SECOND]), "/");

    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    const list = screen.getByRole("region", { name: "Unread notifications" });
    expect(list).toHaveTextContent(FIRST.message);
    expect(list).toHaveTextContent(SECOND.message);
    expect(screen.getByRole("link", { name: FIRST.message })).toHaveAttribute(
      "href",
      `/workspaces/${FIRST.workspace_id}`,
    );
  });

  it("marks one notification read", async () => {
    const requests = renderApp(backend([FIRST, SECOND]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    fireEvent.click(screen.getAllByRole("button", { name: "Mark read" })[0]!);

    expect(await screen.findByRole("button", { name: "Notifications (1)" })).toBeInTheDocument();
    expect(screen.queryByText(FIRST.message)).not.toBeInTheDocument();
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: `/api/v1/notifications/${FIRST.id}/read`,
      csrf: CSRF_TOKEN,
    });
  });

  it("marks them all read", async () => {
    renderApp(backend([FIRST, SECOND]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));

    await waitFor(() =>
      expect(screen.getByText("You have no unread notifications.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Notifications (0)" })).toBeInTheDocument();
  });
});
