// The typed API client. `schema.d.ts` is generated from the backend's OpenAPI spec
// (`npm run generate:api`); never edit it by hand.
import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type ApiClient = ReturnType<typeof createClient<paths>>;
export type ApiErrorBody = components["schemas"]["ErrorBody"];

export interface ApiClientOptions {
  /** Origin of the backend; defaults to the page's own origin. */
  baseUrl?: string;
  fetch?: typeof fetch;
}

export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  return createClient<paths>({
    baseUrl: options.baseUrl ?? window.location.origin,
    fetch: options.fetch,
  });
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
