import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { CSRF_TOKEN, fakeApi, renderApp, setCsrfCookie } from "../test/fakeApi";
import { currentLocation } from "../test/navigation";

const OPEN = { enabled: true, allowed_email_domains: [] };

beforeEach(setCsrfCookie);

async function fillSignUp(fields: {
  email?: string;
  displayName?: string;
  password?: string;
  confirm?: string;
}) {
  const password = fields.password ?? "my own long passphrase";
  fireEvent.change(await screen.findByLabelText("Email"), {
    target: { value: fields.email ?? "grace@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Display name"), {
    target: { value: fields.displayName ?? "Grace Hopper" },
  });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm password"), {
    target: { value: fields.confirm ?? password },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
}

describe("the sign-up link on the login page", () => {
  it("is shown while self-registration is open and leads to the sign-up page", async () => {
    renderApp(fakeApi({ registration: OPEN }), "/login");

    fireEvent.click(await screen.findByRole("link", { name: "Create an account" }));

    expect(await screen.findByRole("heading", { name: "Create an account" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/signup");
  });

  it("is hidden while self-registration is off", async () => {
    const api = fakeApi();
    renderApp(api, "/login");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() =>
      expect(api.requests.some((r) => r.path === "/api/v1/auth/registration")).toBe(true),
    );
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
  });
});

describe("signing up", () => {
  it("creates the account, signs the visitor in and goes home", async () => {
    const api = fakeApi({ registration: OPEN });
    renderApp(api, "/signup");

    await fillSignUp({ email: "grace@example.com", displayName: "Grace Hopper" });

    const header = await screen.findByRole("banner");
    expect(await within(header).findByText("Grace Hopper")).toBeInTheDocument();
    await waitFor(() => expect(currentLocation()).toBe("/"));
    expect(api.requests.find((r) => r.path === "/api/v1/auth/register")).toEqual({
      method: "POST",
      path: "/api/v1/auth/register",
      csrf: CSRF_TOKEN,
      body: {
        email: "grace@example.com",
        display_name: "Grace Hopper",
        password: "my own long passphrase",
      },
    });
  });

  it("shows the API's message when the email's domain is not allowed", async () => {
    renderApp(
      fakeApi({ registration: { enabled: true, allowed_email_domains: ["acme.org"] } }),
      "/signup",
    );

    await fillSignUp({ email: "grace@example.com" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Self-registration is not open to this email domain.",
    );
    expect(currentLocation()).toBe("/signup");
  });

  it("shows the password policy's message", async () => {
    renderApp(fakeApi({ registration: OPEN }), "/signup");

    await fillSignUp({ password: "short" });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The password must be at least 10 characters long.",
    );
  });

  it("does not send the form when the passwords differ", async () => {
    const api = fakeApi({ registration: OPEN });
    renderApp(api, "/signup");

    await fillSignUp({ password: "my own long passphrase", confirm: "my own long passphrasf" });

    expect(await screen.findByRole("alert")).toHaveTextContent("The passwords do not match.");
    expect(api.requests.some((r) => r.path === "/api/v1/auth/register")).toBe(false);
  });

  it("says so, and shows no form, while self-registration is off", async () => {
    renderApp(fakeApi(), "/signup");

    expect(await screen.findByText(/Self-registration is turned off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create account" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
