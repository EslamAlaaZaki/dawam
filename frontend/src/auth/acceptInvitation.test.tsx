import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import { currentLocation } from "../test/navigation";
import {
  CSRF_TOKEN,
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const NEWCOMER: Me = {
  id: "5f0e2d1c-0000-4000-8000-000000000009",
  email: "newcomer@example.com",
  display_name: "New Comer",
  system_role: "user",
  must_change_password: false,
};

const LINK = { email: "newcomer@example.com", expires_at: "2026-01-12T09:00:00Z" };

/** Nobody is signed in until the invitation is accepted. */
function invited(handle: (r: ApiRequest) => Response | undefined = () => undefined) {
  let me: Me | null = null;
  return (request: ApiRequest) => {
    if (request.path === "/api/v1/me") {
      return me ? json(me) : apiError(401, "unauthenticated", "Sign in to continue.");
    }
    if (request.path === "/api/v1/auth/invitations/lookup") {
      return json(LINK);
    }
    const response = handle(request);
    if (request.path === "/api/v1/auth/invitations/accept" && response?.ok) {
      me = NEWCOMER;
    }
    return response;
  };
}

function fillIn(name: string, password: string, confirm = password) {
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: name } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: "Join DAWAM" }));
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => {
  clearCookie();
  window.history.replaceState(null, "", "/");
});

describe("accepting an invitation", () => {
  it("shows which email the invitation is for", async () => {
    window.history.replaceState(null, "", "/accept-invitation#token=tok-123");
    const requests = renderApp(invited(), "/accept-invitation");

    expect(await screen.findByText(/newcomer@example\.com/)).toBeInTheDocument();
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/v1/auth/invitations/lookup",
      query: {},
      csrf: CSRF_TOKEN,
      body: { token: "tok-123" },
    });
  });

  it("sets a display name and password, then signs the new user in", async () => {
    window.history.replaceState(null, "", "/accept-invitation#token=tok-123");
    const requests = renderApp(
      invited((r) => (r.path === "/api/v1/auth/invitations/accept" ? json(NEWCOMER, 201) : undefined)),
      "/accept-invitation",
    );

    await screen.findByText(/newcomer@example\.com/);
    fillIn("New Comer", "a fine new passphrase");

    await waitFor(() => expect(currentLocation()).toBe("/"));
    expect(requests).toContainEqual({
      method: "POST",
      path: "/api/v1/auth/invitations/accept",
      query: {},
      csrf: CSRF_TOKEN,
      body: { token: "tok-123", display_name: "New Comer", password: "a fine new passphrase" },
    });
  });

  it("does not send two different passwords", async () => {
    window.history.replaceState(null, "", "/accept-invitation#token=tok-123");
    const requests = renderApp(invited(), "/accept-invitation");

    await screen.findByText(/newcomer@example\.com/);
    fillIn("New Comer", "a fine new passphrase", "another passphrase");

    expect(await screen.findByRole("alert")).toHaveTextContent("The passwords do not match.");
    expect(requests.some((r) => r.path === "/api/v1/auth/invitations/accept")).toBe(false);
  });

  it("shows the API's message when the password breaks the policy", async () => {
    window.history.replaceState(null, "", "/accept-invitation#token=tok-123");
    renderApp(
      invited((r) =>
        r.path === "/api/v1/auth/invitations/accept"
          ? apiError(422, "invalid_password", "The password must be at least 10 characters long.")
          : undefined,
      ),
      "/accept-invitation",
    );

    await screen.findByText(/newcomer@example\.com/);
    fillIn("New Comer", "tenletters");

    expect(await screen.findByRole("alert")).toHaveTextContent("must be at least 10 characters");
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("says when the link is invalid, used, revoked or expired", async () => {
    window.history.replaceState(null, "", "/accept-invitation#token=old");
    renderApp(
      (r) =>
        r.path === "/api/v1/auth/invitations/lookup"
          ? apiError(400, "invalid_invitation", "This invitation link is invalid.")
          : r.path === "/api/v1/me"
            ? apiError(401, "unauthenticated", "Sign in to continue.")
            : undefined,
      "/accept-invitation",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This invitation link is invalid, used, revoked or expired.",
    );
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
  });

  it("says a link without a token is invalid", async () => {
    renderApp(invited(), "/accept-invitation");

    expect(await screen.findByRole("alert")).toHaveTextContent("This invitation link is invalid");
  });
});
