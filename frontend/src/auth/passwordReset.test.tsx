import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  CSRF_TOKEN,
  apiError,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const anonymous = (handle: (r: ApiRequest) => Response | undefined = () => undefined) =>
  (request: ApiRequest) =>
    request.path === "/api/v1/me"
      ? apiError(401, "unauthenticated", "Sign in to continue.")
      : handle(request);

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => {
  clearCookie();
  window.history.replaceState(null, "", "/");
});

describe("forgot password", () => {
  it("is linked from the sign-in page", async () => {
    renderApp(anonymous(), "/login");

    const link = await screen.findByRole("link", { name: "Forgot your password?" });
    expect(link).toHaveAttribute("href", "/forgot-password");
  });

  it("asks for a reset link and says the same whatever the email", async () => {
    const requests = renderApp(
      anonymous((r) => (r.path === "/api/v1/auth/password/forgot" ? noContent(202) : undefined)),
      "/forgot-password",
    );

    fireEvent.change(await screen.findByLabelText("Email"), {
      target: { value: "grace@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "If an account uses that email, a link to choose a new password is on its way.",
    );
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/v1/auth/password/forgot",
      query: {},
      csrf: CSRF_TOKEN,
      body: { email: "grace@example.com" },
    });
  });
});

describe("reset password", () => {
  function fillIn(password: string, confirm = password) {
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: password } });
    fireEvent.change(screen.getByLabelText("Repeat the new password"), {
      target: { value: confirm },
    });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
  }

  it("sets a new password with the token from the link's fragment", async () => {
    window.history.replaceState(null, "", "/reset-password#token=tok-123");
    const requests = renderApp(
      anonymous((r) => (r.path === "/api/v1/auth/password/reset" ? noContent() : undefined)),
      "/reset-password",
    );

    await screen.findByLabelText("New password");
    fillIn("a brand new password");

    expect(await screen.findByRole("status")).toHaveTextContent("Your password is changed");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/v1/auth/password/reset",
      query: {},
      csrf: CSRF_TOKEN,
      body: { token: "tok-123", password: "a brand new password" },
    });
  });

  it("does not send two different passwords", async () => {
    window.history.replaceState(null, "", "/reset-password#token=tok-123");
    const requests = renderApp(anonymous(), "/reset-password");

    await screen.findByLabelText("New password");
    fillIn("a brand new password", "another password!");

    expect(await screen.findByRole("alert")).toHaveTextContent("The two passwords are different.");
    expect(requests.some((r) => r.path === "/api/v1/auth/password/reset")).toBe(false);
  });

  it("offers a new link when the token is used or expired", async () => {
    window.history.replaceState(null, "", "/reset-password#token=old");
    renderApp(
      anonymous((r) =>
        r.path === "/api/v1/auth/password/reset"
          ? apiError(400, "invalid_reset_token", "This password reset link is invalid.")
          : undefined,
      ),
      "/reset-password",
    );

    await screen.findByLabelText("New password");
    fillIn("a brand new password");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This reset link is invalid, used or expired.",
    );
    expect(screen.getByRole("link", { name: "Ask for a new link" })).toHaveAttribute(
      "href",
      "/forgot-password",
    );
  });

  it("shows the API's message when the password breaks the policy", async () => {
    window.history.replaceState(null, "", "/reset-password#token=tok-123");
    renderApp(
      anonymous((r) =>
        r.path === "/api/v1/auth/password/reset"
          ? apiError(422, "invalid_password", "The password must be at least 10 characters long.")
          : undefined,
      ),
      "/reset-password",
    );

    await screen.findByLabelText("New password");
    fillIn("tenletters");

    expect(await screen.findByRole("alert")).toHaveTextContent("must be at least 10 characters");
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });

  it("says a link without a token is invalid", async () => {
    renderApp(anonymous(), "/reset-password");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("This reset link is invalid"),
    );
  });
});
