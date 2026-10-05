// Render the app at a path against a fake API: each test answers the requests it cares
// about, and the fake checks the CSRF token on every state-changing request.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import { setLocation } from "./navigation";
import { TestApp } from "./TestApp";

export const CSRF_TOKEN = "csrf-token-from-the-cookie";

export interface ApiRequest {
  method: string;
  path: string;
  /** The query parameters, e.g. `{ q: "grace" }`. */
  query: Record<string, string>;
  csrf: string | null;
  body: unknown;
}

/** Answers a request, or returns `undefined` for a 404. */
export type FakeApi = (request: ApiRequest) => Response | undefined | Promise<Response | undefined>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message, details: {} } }, status);
}

export function noContent(status = 204): Response {
  return new Response(null, { status });
}

/** Sets the CSRF cookie the client echoes; call in `beforeEach`, undo with the result. */
export function setCsrfCookie(): () => void {
  document.cookie = `dawam_csrf=${CSRF_TOKEN}; path=/`;
  return () => {
    document.cookie = "dawam_csrf=; path=/; max-age=0";
  };
}

/** Renders the app at `path`; returns every request the app made. */
export function renderApp(api: FakeApi, path: string): ApiRequest[] {
  setLocation(path);
  const requests: ApiRequest[] = [];
  const fetchStub = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const text = await request.text();
    const url = new URL(request.url);
    const recorded: ApiRequest = {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      csrf: request.headers.get("X-CSRF-Token"),
      // JSON bodies are parsed; a multipart upload is left as its raw text.
      body: text
        ? request.headers.get("Content-Type")?.includes("multipart/form-data")
          ? text
          : JSON.parse(text)
        : undefined,
    };
    requests.push(recorded);
    if (recorded.method !== "GET" && recorded.csrf !== CSRF_TOKEN) {
      return apiError(403, "csrf_failed", "The request has no valid CSRF token.");
    }
    if (`${recorded.method} ${recorded.path}` === "GET /api/v1/version") {
      return json({ name: "DAWAM", version: "1.2.3" });
    }
    return (await api(recorded)) ?? apiError(404, "not_found", "Not Found");
  };
  const client = createApiClient({ baseUrl: "http://dawam.test", fetch: fetchStub });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <TestApp />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
  return requests;
}
