import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    read_at: null,
  };
}

const FIRST = note("a0000000-0000-4000-8000-000000000001", "Grace is the new owner of Retail DW.");
const SECOND = note("a0000000-0000-4000-8000-000000000002", "Alan is the new owner of Sales DW.");

const PAGE = 2;

/** An in-memory backend paging `all` (newest first) by index, as the API does. */
function backend(seed: Notification[], visited: ApiRequest[] = []) {
  const all = seed.map((n) => ({ ...n }));
  const unreadCount = () => all.filter((n) => n.read_at === null).length;
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case "GET /api/v1/notifications": {
        visited.push(r);
        const from = Number(r.query.cursor ?? 0);
        const items = all.slice(from, from + PAGE);
        const next = from + PAGE < all.length ? String(from + PAGE) : null;
        return json({ items, unread_count: unreadCount(), next_cursor: next });
      }
      case "POST /api/v1/notifications/read-all":
        all.forEach((n) => (n.read_at ||= "2026-01-05T10:00:00Z"));
        return noContent();
    }
    const read = /^\/api\/v1\/notifications\/([^/]+)\/read$/.exec(r.path);
    if (r.method === "POST" && read) {
      const found = all.find((n) => n.id === read[1]);
      if (found) found.read_at ||= "2026-01-05T10:00:00Z";
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

    const list = screen.getByRole("region", { name: "Notifications" });
    expect(list).toHaveTextContent(FIRST.message);
    expect(list).toHaveTextContent(SECOND.message);
    expect(screen.getByRole("link", { name: FIRST.message })).toHaveAttribute(
      "href",
      `/workspaces/${FIRST.workspace_id}`,
    );
  });

  it("marks one notification read but keeps it listed, styled as read", async () => {
    const requests = renderApp(backend([FIRST, SECOND]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    fireEvent.click(screen.getAllByRole("button", { name: "Mark read" })[0]!);

    expect(await screen.findByRole("button", { name: "Notifications (1)" })).toBeInTheDocument();
    expect(screen.getByText(FIRST.message)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: FIRST.message })).toHaveClass("text-muted-foreground");
    expect(screen.getAllByRole("button", { name: "Mark read" })).toHaveLength(1);
    expect(requests.find((r) => r.method === "POST")).toMatchObject({
      path: `/api/v1/notifications/${FIRST.id}/read`,
      csrf: CSRF_TOKEN,
    });
  });

  it("marks a notification read when its row is clicked", async () => {
    const requests = renderApp(backend([FIRST, SECOND]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    fireEvent.click(screen.getByText(SECOND.message));

    expect(await screen.findByRole("button", { name: "Notifications (1)" })).toBeInTheDocument();
    expect(requests.filter((r) => r.method === "POST")).toHaveLength(1);
    expect(requests.find((r) => r.method === "POST")?.path).toBe(
      `/api/v1/notifications/${SECOND.id}/read`,
    );
  });

  it("marks them all read and keeps them listed", async () => {
    renderApp(backend([FIRST, SECOND]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (2)" }));

    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));

    expect(await screen.findByRole("button", { name: "Notifications (0)" })).toBeInTheDocument();
    expect(screen.getByText(FIRST.message)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark all read" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark read" })).not.toBeInTheDocument();
  });

  it("says so when there are no notifications at all", async () => {
    renderApp(backend([]), "/");
    fireEvent.click(await screen.findByRole("button", { name: "Notifications (0)" }));

    expect(screen.getByText("You have no notifications.")).toBeInTheDocument();
  });

  describe("loading older notifications on scroll", () => {
    let intersect: (() => void) | undefined;
    beforeEach(() => {
      vi.stubGlobal(
        "IntersectionObserver",
        class {
          constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
            intersect = () => callback([{ isIntersecting: true }]);
          }
          observe() {}
          disconnect() {}
        },
      );
    });
    afterEach(() => {
      vi.unstubAllGlobals();
      intersect = undefined;
    });

    it("fetches the next page when the end is reached, then says there are no older ones", async () => {
      const third = note(
        "a0000000-0000-4000-8000-000000000003",
        "Edsger is the new owner of HR DW.",
      );
      const visited: ApiRequest[] = [];
      renderApp(backend([FIRST, SECOND, third], visited), "/");
      fireEvent.click(await screen.findByRole("button", { name: "Notifications (3)" }));
      expect(screen.queryByText(third.message)).not.toBeInTheDocument();
      expect(screen.queryByText("No older notifications")).not.toBeInTheDocument();

      await waitFor(() => expect(intersect).toBeDefined());
      act(() => intersect!());

      expect(await screen.findByText(third.message)).toBeInTheDocument();
      expect(screen.getByText("No older notifications")).toBeInTheDocument();
      expect(visited).toHaveLength(2);
    });
  });
});
