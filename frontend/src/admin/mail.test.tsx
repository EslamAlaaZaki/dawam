import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../api/queries";
import type { SmtpSettings, UndeliveredLink } from "../api/mail";
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
const ADA: Me = { ...ROOT, email: "ada@example.com", display_name: "Ada", system_role: "user" };

const SAVED: SmtpSettings = {
  host: "smtp.example.com",
  port: 587,
  security: "starttls",
  username: "dawam",
  has_password: true,
  sender: "dawam@example.com",
  updated_at: "2026-01-05T09:00:00Z",
};

const LINK: UndeliveredLink = {
  id: "5d3a8b1e-0000-4000-8000-000000000001",
  recipient: "grace@example.com",
  subject: "Reset your DAWAM password",
  purpose: "password_reset",
  url: "http://localhost:8000/reset-password#token=abc",
  reason: "smtp_not_configured",
  created_at: "2026-01-05T09:00:00Z",
  expires_at: "2026-01-05T09:30:00Z",
};

function as(me: Me, handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  return (request: ApiRequest) => (request.path === "/api/v1/me" ? json(me) : handle(request));
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("admin navigation", () => {
  it("shows the email pages to admins", async () => {
    renderApp(as(ROOT), "/");

    const nav = await screen.findByRole("navigation", { name: "Administration" });
    expect(within(nav).getByRole("link", { name: "Email settings" })).toHaveAttribute(
      "href",
      "/admin/email",
    );
    expect(within(nav).getByRole("link", { name: "Links to share" })).toHaveAttribute(
      "href",
      "/admin/email/links",
    );
  });

  it("hides them from other users, who cannot open them either", async () => {
    renderApp(as(ADA), "/admin/email");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Only an administrator can see this page.",
    );
    expect(screen.queryByRole("navigation", { name: "Administration" })).not.toBeInTheDocument();
  });
});

describe("email settings", () => {
  it("says SMTP is off and saves new settings", async () => {
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path !== "/api/v1/admin/smtp") return undefined;
        return r.method === "PUT" ? json({ ...SAVED, ...(r.body as object) }) : json(null);
      }),
      "/admin/email",
    );

    expect(await screen.findByText(/SMTP is off/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("SMTP host"), { target: { value: "smtp.example.com" } });
    fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: "dawam" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "smtp secret" } });
    fireEvent.change(screen.getByLabelText("From address"), {
      target: { value: "dawam@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("DAWAM sends email through this SMTP server.")).toBeInTheDocument();
    expect(requests).toContainEqual({
      method: "PUT",
      path: "/api/v1/admin/smtp",
      query: {},
      csrf: CSRF_TOKEN,
      body: {
        host: "smtp.example.com",
        port: 587,
        security: "starttls",
        sender: "dawam@example.com",
        username: "dawam",
        password: "smtp secret",
      },
    });
  });

  it("keeps the saved password when the field is left empty", async () => {
    const requests = renderApp(
      as(ROOT, (r) => (r.path === "/api/v1/admin/smtp" ? json(SAVED) : undefined)),
      "/admin/email",
    );

    const password = await screen.findByLabelText("Password");
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", "Saved: leave empty to keep it");
    expect(screen.getByLabelText("SMTP host")).toHaveValue("smtp.example.com");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(requests.some((r) => r.method === "PUT")).toBe(true));
    const put = requests.find((r) => r.method === "PUT");
    expect(put?.body).not.toHaveProperty("password");
  });

  it("sends a test email and shows why one failed", async () => {
    let attempt = 0;
    const requests = renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/smtp") return json(SAVED);
        if (r.path === "/api/v1/admin/smtp/test") {
          attempt += 1;
          return attempt === 1
            ? json({ to: ROOT.email })
            : apiError(502, "smtp_failed", "The SMTP server rejected the username or password.");
        }
        return undefined;
      }),
      "/admin/email",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Send test email" }));
    expect(await screen.findByText("Test email sent to root@example.com.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Send test email" }));
    expect(
      await screen.findByText("The SMTP server rejected the username or password."),
    ).toBeInTheDocument();
    expect(requests.filter((r) => r.path === "/api/v1/admin/smtp/test")[0]?.body).toEqual({
      to: null,
    });
  });

  it("turns SMTP off", async () => {
    renderApp(
      as(ROOT, (r) => {
        if (r.path !== "/api/v1/admin/smtp") return undefined;
        return r.method === "DELETE" ? noContent() : json(SAVED);
      }),
      "/admin/email",
    );

    fireEvent.click(await screen.findByRole("button", { name: "Turn SMTP off" }));

    expect(await screen.findByText(/SMTP is off/)).toBeInTheDocument();
  });
});

describe("links to share", () => {
  it("lists links with a copy button, and removes one", async () => {
    let links = [LINK];
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderApp(
      as(ROOT, (r) => {
        if (r.path === "/api/v1/admin/undelivered-links") return json({ items: links });
        if (r.method === "DELETE" && r.path === `/api/v1/admin/undelivered-links/${LINK.id}`) {
          links = [];
          return noContent();
        }
        return undefined;
      }),
      "/admin/email/links",
    );

    const item = await screen.findByRole("listitem", { name: "Link for grace@example.com" });
    expect(within(item).getByText("Password reset")).toBeInTheDocument();
    expect(within(item).getByLabelText("Link")).toHaveValue(LINK.url);
    fireEvent.click(within(item).getByRole("button", { name: "Copy link" }));
    expect(await within(item).findByRole("button", { name: "Copied" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith(LINK.url);

    fireEvent.click(within(item).getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("There are no links to share.")).toBeInTheDocument();
  });
});
