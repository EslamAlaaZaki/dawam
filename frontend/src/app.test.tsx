import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { createApiClient } from "./api/client";
import { ApiClientContext } from "./api/context";
import type { Me } from "./api/queries";
import { currentLocation, setLocation } from "./test/navigation";
import { TestApp } from "./test/TestApp";

const CSRF_TOKEN = "csrf-token-from-the-cookie";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};
const ADA_PASSWORD = "analytical engine";

interface Recorded {
  method: string;
  path: string;
  csrf: string | null;
  body: unknown;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message, details: {} } }, status);
}

/** A stand-in for the backend: one account (Ada), a session flag and the CSRF check. */
function fakeBackend(options: { signedIn?: boolean; versionResponse?: () => Response } = {}) {
  let signedIn = options.signedIn ?? false;
  const requests: Recorded[] = [];

  async function handle(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    const text = await request.text();
    const body = text ? JSON.parse(text) : undefined;
    const csrf = request.headers.get("X-CSRF-Token");
    requests.push({ method: request.method, path, csrf, body });

    if (request.method !== "GET" && csrf !== CSRF_TOKEN) {
      return apiError(403, "csrf_failed", "The request has no valid CSRF token.");
    }
    switch (`${request.method} ${path}`) {
      case "GET /api/v1/version":
        return options.versionResponse?.() ?? json({ name: "DAWAM", version: "1.2.3" });
      case "GET /api/v1/me":
        return signedIn ? json(ADA) : apiError(401, "unauthenticated", "Sign in to continue.");
      case "POST /api/v1/auth/login":
        if (body?.email === ADA.email && body?.password === ADA_PASSWORD) {
          signedIn = true;
          return json(ADA);
        }
        return apiError(401, "invalid_credentials", "The email or password is incorrect.");
      case "POST /api/v1/auth/logout":
        signedIn = false;
        return new Response(null, { status: 204 });
      case "GET /api/v1/workspaces":
        return signedIn
          ? json({ items: [], next_cursor: null })
          : apiError(401, "unauthenticated", "Sign in to continue.");
      default:
        return apiError(404, "not_found", "Not Found");
    }
  }

  return { handle, requests };
}

function renderApp(backend: ReturnType<typeof fakeBackend>, path = "/") {
  setLocation(path);
  const fetchStub = async (input: RequestInfo | URL, init?: RequestInit) =>
    backend.handle(new Request(input, init));
  const client = createApiClient({ baseUrl: "http://dawam.test", fetch: fetchStub });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <TestApp />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
}

function currentPath() {
  return currentLocation().split("?")[0];
}

async function submitSignIn(email: string, password: string) {
  fireEvent.change(await screen.findByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  document.cookie = `dawam_csrf=${CSRF_TOKEN}; path=/`;
});

afterEach(() => {
  document.cookie = "dawam_csrf=; path=/; max-age=0";
});

describe("signing in", () => {
  it("sends an anonymous visitor to the login page", async () => {
    renderApp(fakeBackend());

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/login");
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("signs in with email and password, sending the CSRF token", async () => {
    const backend = fakeBackend();
    renderApp(backend);

    await submitSignIn(ADA.email, ADA_PASSWORD);

    const header = await screen.findByRole("banner");
    expect(await within(header).findByText("Ada Lovelace")).toBeInTheDocument();
    await waitFor(() => expect(currentLocation()).toBe("/"));
    expect(await screen.findByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
    const login = backend.requests.find((r) => r.path === "/api/v1/auth/login");
    expect(login).toEqual({
      method: "POST",
      path: "/api/v1/auth/login",
      csrf: CSRF_TOKEN,
      body: { email: ADA.email, password: ADA_PASSWORD },
    });
  });

  it("shows the API's message when the credentials are wrong", async () => {
    renderApp(fakeBackend());

    await submitSignIn(ADA.email, "not the password");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The email or password is incorrect.",
    );
    expect(currentPath()).toBe("/login");
  });

  it("sends a signed-in user away from the login page", async () => {
    renderApp(fakeBackend({ signedIn: true }), "/login");

    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    await waitFor(() => expect(currentLocation()).toBe("/"));
  });

  it("redirects unknown pages of an anonymous visitor to the login page", async () => {
    renderApp(fakeBackend(), "/workspaces/42");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/login?from=%2Fworkspaces%2F42");
  });

  it("returns to the page the visitor asked for after signing in", async () => {
    renderApp(fakeBackend(), "/?view=recent");

    await submitSignIn(ADA.email, ADA_PASSWORD);

    await waitFor(() => expect(currentLocation()).toBe("/?view=recent"));
    expect(await screen.findByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
  });

  it.each([
    "https://evil.example/",
    "//evil.example/",
    "/\\evil.example/",
    "/\t/evil.example/",
    "/.//evil.example/",
    "/a/..//evil.example/",
    "/./\\evil.example/",
    "/%2F%2Fevil.example/",
    "/.%2F%2Fevil.example/",
    "/%5C%5Cevil.example/",
    "javascript:alert(1)",
    "/login",
  ])("never follows a return path that leaves the site or loops (%s)", async (from) => {
    renderApp(fakeBackend(), `/login?from=${encodeURIComponent(from)}`);

    await submitSignIn(ADA.email, ADA_PASSWORD);

    await waitFor(() => expect(currentLocation()).toBe("/"));
  });

  it("sends a signed-in user on an unknown page home", async () => {
    renderApp(fakeBackend({ signedIn: true }), "/no-such-page");

    expect(await screen.findByRole("heading", { name: "Workspaces" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/");
  });

  it("says so when the session cannot be checked", async () => {
    const backend = fakeBackend();
    const handle = backend.handle;
    backend.handle = async (request) =>
      new URL(request.url).pathname === "/api/v1/me"
        ? apiError(503, "not_ready", "DAWAM is not ready.")
        : handle(request);
    renderApp(backend);

    expect(await screen.findByText("Could not check your session: DAWAM is not ready.")).toBeInTheDocument();
    expect(currentLocation()).toBe("/");
  });
});

describe("the signed-in shell", () => {
  it("shows the product name and the user's display name in the header", async () => {
    renderApp(fakeBackend({ signedIn: true }));

    const header = await screen.findByRole("banner");
    expect(within(header).getByRole("heading", { name: "DAWAM" })).toBeInTheDocument();
    expect(await within(header).findByText("Ada Lovelace")).toBeInTheDocument();
  });

  it("signs out on the server and returns to the login page", async () => {
    const backend = fakeBackend({ signedIn: true });
    renderApp(backend);

    fireEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(currentLocation()).toBe("/login");
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
    const logout = backend.requests.find((r) => r.path === "/api/v1/auth/logout");
    expect(logout).toMatchObject({ method: "POST", csrf: CSRF_TOKEN });
  });
});

describe("the API status footer", () => {
  it("shows the API version fetched through the generated client", async () => {
    const backend = fakeBackend();
    renderApp(backend);

    expect(await screen.findByText("API version 1.2.3")).toBeInTheDocument();
    expect(backend.requests).toContainEqual({
      method: "GET",
      path: "/api/v1/version",
      csrf: null,
      body: undefined,
    });
  });

  it("shows the API's error message when the API fails", async () => {
    renderApp(
      fakeBackend({
        versionResponse: () => apiError(503, "not_ready", "DAWAM is not ready."),
      }),
    );

    expect(await screen.findByText("API unavailable: DAWAM is not ready.")).toBeInTheDocument();
  });
});
