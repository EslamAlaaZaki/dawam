// The typed API client. `schema.d.ts` is generated from the backend's OpenAPI spec
// (`npm run generate:api`); never edit it by hand.
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";

export type ApiClient = ReturnType<typeof createClient<paths>>;
export type ApiErrorBody = components["schemas"]["ErrorBody"];

/** The backend's double-submit CSRF cookie and the header it must be echoed in. */
export const CSRF_COOKIE = "dawam_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);

export interface ApiClientOptions {
  /** Origin of the backend; defaults to the page's own origin. */
  baseUrl?: string;
  fetch?: typeof fetch;
}

/** The value of a cookie readable by scripts on this page, if it is set. */
export function readCookie(name: string): string | undefined {
  for (const part of document.cookie.split(";")) {
    const [key, ...value] = part.trim().split("=");
    if (key === name) {
      return decodeURIComponent(value.join("="));
    }
  }
  return undefined;
}

// Every state-changing request carries the CSRF token the backend put in the cookie.
const sendCsrfToken: Middleware = {
  onRequest({ request }) {
    if (!SAFE_METHODS.has(request.method.toUpperCase())) {
      const token = readCookie(CSRF_COOKIE);
      if (token !== undefined) {
        request.headers.set(CSRF_HEADER, token);
      }
    }
    return request;
  },
};

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const client = createClient<paths>({
    baseUrl: options.baseUrl ?? window.location.origin,
    fetch: options.fetch,
  });
  client.use(sendCsrfToken);
  return client;
}

/** Thrown by query functions when the API answers with the standard error shape. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorBody["details"];

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
  }
}
