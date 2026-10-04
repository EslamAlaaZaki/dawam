// A small in-memory stand-in for the backend's account API (sign-in, sign-up, profile,
// admin settings), and a way to render the app against it. Used by the component tests
// of those screens; each test arranges the state it needs.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import type { Me } from "../api/queries";
import { setLocation } from "./navigation";
import { TestApp } from "./TestApp";

export const CSRF_TOKEN = "csrf-token-from-the-cookie";

export interface Recorded {
  method: string;
  path: string;
  csrf: string | null;
  body: unknown;
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message, details: {} } }, status);
}

interface Account {
  me: Me;
  password: string;
}

export interface FakeApiOptions {
  /** The accounts that exist; the first one is signed in when `signedIn` is set. */
  accounts?: Account[];
  signedIn?: boolean;
  registration?: { enabled: boolean; allowed_email_domains: string[] };
}

export function user(overrides: Partial<Me> = {}): Me {
  return {
    id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
    email: "ada@example.com",
    display_name: "Ada Lovelace",
    system_role: "user",
    ...overrides,
  };
}

export function fakeApi(options: FakeApiOptions = {}) {
  const accounts = options.accounts ?? [];
  const state = {
    current: options.signedIn ? (accounts[0] ?? null) : null,
    otherSessions: options.signedIn ? 1 : 0,
    registration: options.registration ?? { enabled: false, allowed_email_domains: [] },
  };
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
    const current = state.current;
    const signedInOnly = new Set([
      "PATCH /api/v1/me",
      "GET /api/v1/me",
      "POST /api/v1/auth/password/change",
      "POST /api/v1/auth/logout-all",
      "GET /api/v1/admin/settings",
      "PUT /api/v1/admin/settings",
    ]);
    const route = `${request.method} ${path}`;
    if (signedInOnly.has(route) && current === null) {
      return apiError(401, "unauthenticated", "Sign in to continue.");
    }
    switch (route) {
      case "GET /api/v1/version":
        return json({ name: "DAWAM", version: "1.2.3" });
      case "GET /api/v1/me":
        return json(current!.me);
      case "PATCH /api/v1/me": {
        const name = String(body.display_name).trim();
        if (!name) {
          return apiError(422, "invalid_display_name", "The display name must not be empty.");
        }
        current!.me = { ...current!.me, display_name: name };
        return json(current!.me);
      }
      case "POST /api/v1/auth/login": {
        const account = accounts.find(
          (a) => a.me.email === body?.email && a.password === body?.password,
        );
        if (!account) {
          return apiError(401, "invalid_credentials", "The email or password is incorrect.");
        }
        state.current = account;
        return json(account.me);
      }
      case "POST /api/v1/auth/logout":
        state.current = null;
        return new Response(null, { status: 204 });
      case "POST /api/v1/auth/logout-all":
        state.current = null;
        state.otherSessions = 0;
        return new Response(null, { status: 204 });
      case "POST /api/v1/auth/password/change":
        if (body.current_password !== current!.password) {
          return apiError(400, "wrong_password", "The current password is incorrect.");
        }
        if (String(body.new_password).length < 10) {
          return apiError(
            422,
            "invalid_password",
            "The password must be at least 10 characters long.",
          );
        }
        current!.password = body.new_password;
        state.otherSessions = 0;
        return new Response(null, { status: 204 });
      case "GET /api/v1/auth/registration":
        return json({ open: state.registration.enabled });
      case "POST /api/v1/auth/register": {
        if (!state.registration.enabled) {
          return apiError(403, "registration_closed", "Self-registration is turned off.");
        }
        const email = String(body.email).trim().toLowerCase();
        const domains = state.registration.allowed_email_domains;
        if (domains.length > 0 && !domains.includes(email.split("@").pop() ?? "")) {
          return apiError(
            403,
            "email_domain_not_allowed",
            "Self-registration is not open to this email domain.",
          );
        }
        if (String(body.password).length < 10) {
          return apiError(
            422,
            "invalid_password",
            "The password must be at least 10 characters long.",
          );
        }
        const account = {
          me: user({ id: crypto.randomUUID(), email, display_name: body.display_name.trim() }),
          password: body.password,
        };
        accounts.push(account);
        state.current = account;
        return json(account.me, 201);
      }
      case "GET /api/v1/admin/settings":
      case "PUT /api/v1/admin/settings": {
        if (current!.me.system_role !== "admin") {
          return apiError(403, "forbidden", "Only admins can do this.");
        }
        if (request.method === "PUT" && body.registration) {
          const bad = body.registration.allowed_email_domains.find(
            (d: string) => !d.includes("."),
          );
          if (bad !== undefined) {
            return apiError(
              422,
              "invalid_email_domain",
              `'${bad}' is not a domain name (e.g. example.com).`,
            );
          }
          state.registration = body.registration;
        }
        return json({ registration: state.registration });
      }
      default:
        return apiError(404, "not_found", "Not Found");
    }
  }

  return { handle, requests, state, accounts };
}

export type FakeApi = ReturnType<typeof fakeApi>;

/** Render the whole app at `path`, its API client talking to `api`. */
export function renderApp(api: FakeApi, path = "/") {
  setLocation(path);
  const fetchStub = async (input: RequestInfo | URL, init?: RequestInit) =>
    api.handle(new Request(input, init));
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

/** The CSRF cookie the backend would have set; call in `beforeEach`. */
export function setCsrfCookie(): () => void {
  document.cookie = `dawam_csrf=${CSRF_TOKEN}; path=/`;
  return () => {
    document.cookie = "dawam_csrf=; path=/; max-age=0";
  };
}
