import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { CSRF_TOKEN, fakeApi, renderApp, setCsrfCookie, user } from "../test/fakeApi";
import { currentLocation } from "../test/navigation";

const PASSWORD = "analytical engine";

beforeEach(setCsrfCookie);

function signedInAsAda() {
  return fakeApi({ accounts: [{ me: user(), password: PASSWORD }], signedIn: true });
}

function passwordSection() {
  return screen.getByRole("region", { name: "Change password" });
}

async function changePassword(current: string, next: string, confirm = next) {
  const section = await screen.findByRole("region", { name: "Change password" });
  fireEvent.change(within(section).getByLabelText("Current password"), {
    target: { value: current },
  });
  fireEvent.change(within(section).getByLabelText("New password"), { target: { value: next } });
  fireEvent.change(within(section).getByLabelText("Confirm new password"), {
    target: { value: confirm },
  });
  fireEvent.click(within(section).getByRole("button", { name: "Change password" }));
}

describe("the profile page", () => {
  it("is reached from the user's name in the header", async () => {
    renderApp(signedInAsAda());

    const header = await screen.findByRole("banner");
    fireEvent.click(await within(header).findByRole("link", { name: "Ada Lovelace" }));

    expect(await screen.findByRole("heading", { name: "Your profile" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/profile");
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
  });

  it("sends an anonymous visitor to sign in first", async () => {
    renderApp(fakeApi(), "/profile");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/login?from=%2Fprofile");
  });
});

describe("editing the display name", () => {
  it("saves the new name and shows it in the header", async () => {
    const api = signedInAsAda();
    renderApp(api, "/profile");

    const input = await screen.findByLabelText("Display name");
    expect(input).toHaveValue("Ada Lovelace");
    fireEvent.change(input, { target: { value: "Countess of Lovelace" } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(await screen.findByText("Display name saved.")).toBeInTheDocument();
    const header = screen.getByRole("banner");
    expect(within(header).getByText("Countess of Lovelace")).toBeInTheDocument();
    expect(api.requests.find((r) => r.method === "PATCH")).toEqual({
      method: "PATCH",
      path: "/api/v1/me",
      csrf: CSRF_TOKEN,
      body: { display_name: "Countess of Lovelace" },
    });
  });

  it("shows the API's message for an empty name", async () => {
    renderApp(signedInAsAda(), "/profile");

    fireEvent.change(await screen.findByLabelText("Display name"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "Save name" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The display name must not be empty.",
    );
  });
});

describe("changing the password", () => {
  it("sends the current and the new password and says other sessions ended", async () => {
    const api = signedInAsAda();
    renderApp(api, "/profile");

    await changePassword(PASSWORD, "a brand new passphrase");

    expect(
      await within(passwordSection()).findByText(
        "Password changed. Your other sessions have been signed out.",
      ),
    ).toBeInTheDocument();
    expect(within(passwordSection()).getByLabelText("Current password")).toHaveValue("");
    expect(api.requests.find((r) => r.path === "/api/v1/auth/password/change")).toEqual({
      method: "POST",
      path: "/api/v1/auth/password/change",
      csrf: CSRF_TOKEN,
      body: { current_password: PASSWORD, new_password: "a brand new passphrase" },
    });
    expect(api.state.current?.password).toBe("a brand new passphrase");
  });

  it("shows the API's message when the current password is wrong", async () => {
    renderApp(signedInAsAda(), "/profile");

    await changePassword("not my password", "a brand new passphrase");

    expect(await within(passwordSection()).findByRole("alert")).toHaveTextContent(
      "The current password is incorrect.",
    );
  });

  it("does not send the form when the new passwords differ", async () => {
    const api = signedInAsAda();
    renderApp(api, "/profile");

    await changePassword(PASSWORD, "a brand new passphrase", "a brand new passphrasf");

    expect(await within(passwordSection()).findByRole("alert")).toHaveTextContent(
      "The new passwords do not match.",
    );
    expect(api.requests.some((r) => r.path === "/api/v1/auth/password/change")).toBe(false);
  });
});

describe("signing out everywhere", () => {
  it("ends every session and returns to the login page", async () => {
    const api = signedInAsAda();
    renderApp(api, "/profile");

    fireEvent.click(await screen.findByRole("button", { name: "Sign out everywhere" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    await waitFor(() => expect(currentLocation()).toBe("/login"));
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
    expect(api.requests.find((r) => r.path === "/api/v1/auth/logout-all")).toMatchObject({
      method: "POST",
      csrf: CSRF_TOKEN,
    });
  });
});
