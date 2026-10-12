import {
  cleanup,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import { openMenu } from "../test/menus";
import {
  json,
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
const ROOT: Me = { ...ADA, display_name: "Root Admin", system_role: "admin" };

function backend(me: Me) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(me);
      case "GET /api/v1/workspaces":
        return json({ items: [], next_cursor: null });
      case "GET /api/v1/notifications":
        return json({ items: [], unread_count: 0 });
      case "GET /api/v1/admin/llm/setup":
        return json({ complete: true });
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

async function openUserMenu(name: string) {
  const header = await screen.findByRole("banner");
  openMenu(await within(header).findByRole("button", { name }));
}

describe("the theme choice in the user menu", () => {
  it("offers System, Light and Dark, starting on System", async () => {
    renderApp(backend(ADA), "/");
    await openUserMenu("Ada Lovelace");

    expect(
      await screen.findByRole("menuitemradio", { name: "System" }),
    ).toBeChecked();
    expect(
      screen.getByRole("menuitemradio", { name: "Light" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("menuitemradio", { name: "Dark" }),
    ).not.toBeChecked();
  });

  it("applies the choice at once and keeps it across a reload", async () => {
    renderApp(backend(ADA), "/");
    await openUserMenu("Ada Lovelace");
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "Dark" }));

    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    expect(localStorage.getItem("theme")).toBe("dark");

    cleanup(); // a reload: the page is rebuilt, the browser's storage stays
    document.documentElement.className = "";
    renderApp(backend(ADA), "/");
    await openUserMenu("Ada Lovelace");

    expect(
      await screen.findByRole("menuitemradio", { name: "Dark" }),
    ).toBeChecked();
    expect(document.documentElement).toHaveClass("dark");
  });
});

describe("the sidebar", () => {
  it("links to the Workspaces list, and to the admin pages for admins only", async () => {
    renderApp(backend(ADA), "/");
    const sidebar = await screen.findByRole("complementary", {
      name: "Navigation",
    });
    expect(
      within(sidebar).getByRole("link", { name: "Workspaces" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      screen.queryByRole("navigation", { name: "Administration" }),
    ).not.toBeInTheDocument();
    cleanup();

    renderApp(backend(ROOT), "/");
    const entry = await screen.findByRole("navigation", {
      name: "Administration",
    });
    expect(within(entry).getByRole("link", { name: "Admin" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
    cleanup();

    renderApp(backend(ROOT), "/admin/users");
    const admin = await screen.findByRole("navigation", {
      name: "Administration",
    });
    expect(within(admin).getByRole("link", { name: "Users" })).toHaveAttribute(
      "href",
      "/admin/users",
    );
  });

  it("opens as a drawer from the top bar and closes with Escape", async () => {
    renderApp(backend(ADA), "/");
    const header = await screen.findByRole("banner");
    const open = await within(header).findByRole("button", {
      name: "Open navigation",
    });

    fireEvent.click(open);

    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    expect(
      within(drawer).getByRole("link", { name: "Workspaces" }),
    ).toBeInTheDocument();
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });
});

describe("the user menu", () => {
  it("links to the profile and the password change, and works from the keyboard", async () => {
    renderApp(backend(ADA), "/");
    await openUserMenu("Ada Lovelace");

    expect(
      await screen.findByRole("menuitem", { name: "Profile" }),
    ).toHaveAttribute("href", "/profile");
    expect(
      screen.getByRole("menuitem", { name: "Change password" }),
    ).toHaveAttribute("href", "/profile#password-title");
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument(),
    );
  });
});
