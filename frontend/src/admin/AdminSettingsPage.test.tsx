import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { CSRF_TOKEN, fakeApi, renderApp, setCsrfCookie, user } from "../test/fakeApi";
import { currentLocation } from "../test/navigation";

beforeEach(setCsrfCookie);

const ROOT = user({
  id: "0b8e5a5e-3c1d-4f7e-9a51-1f2e3d4c5b6a",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
});

const CLOSED = { enabled: false, allowed_email_domains: [] as string[] };

function signedInAs(me = ROOT, registration = CLOSED) {
  return fakeApi({
    accounts: [{ me, password: "installation keeper" }],
    signedIn: true,
    registration: { ...registration },
  });
}

describe("the admin settings page", () => {
  it("is linked from the header for admins and shows the current settings", async () => {
    renderApp(signedInAs(ROOT, { enabled: true, allowed_email_domains: ["example.com"] }));

    const header = await screen.findByRole("banner");
    fireEvent.click(await within(header).findByRole("link", { name: "Admin settings" }));

    expect(await screen.findByRole("heading", { name: "Admin settings" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/admin/settings");
    expect(await screen.findByLabelText("Allow self-registration")).toBeChecked();
    expect(screen.getByLabelText("Allowed email domains")).toHaveValue("example.com");
  });

  it("turns self-registration on for the listed domains", async () => {
    const api = signedInAs();
    renderApp(api, "/admin/settings");

    const toggle = await screen.findByLabelText("Allow self-registration");
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText("Allowed email domains"), {
      target: { value: " example.com \n\nacme.org\n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    expect(api.requests.find((r) => r.method === "PUT")).toEqual({
      method: "PUT",
      path: "/api/v1/admin/settings",
      csrf: CSRF_TOKEN,
      body: {
        registration: { enabled: true, allowed_email_domains: ["example.com", "acme.org"] },
      },
    });
    expect(api.state.registration).toEqual({
      enabled: true,
      allowed_email_domains: ["example.com", "acme.org"],
    });
  });

  it("shows the API's message for an invalid domain", async () => {
    renderApp(signedInAs(), "/admin/settings");

    fireEvent.change(await screen.findByLabelText("Allowed email domains"), {
      target: { value: "localhost" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "'localhost' is not a domain name (e.g. example.com).",
    );
  });

  it("is not offered to regular users, and tells them it is for admins", async () => {
    const api = signedInAs(user());
    renderApp(api, "/admin/settings");

    expect(await screen.findByText("Only admins can change these settings.")).toBeInTheDocument();
    const header = screen.getByRole("banner");
    expect(within(header).queryByRole("link", { name: "Admin settings" })).not.toBeInTheDocument();
    expect(api.requests.some((r) => r.path === "/api/v1/admin/settings")).toBe(false);
  });
});
