// Runs before every request `web` serves itself (never the proxied API, see
// `config.matcher`) and adds the per-request security headers.
import { NextResponse, type NextRequest } from "next/server";

import {
  contentSecurityPolicy,
  hstsMaxAgeSeconds,
  newNonce,
  strictTransportSecurity,
} from "./security/headers";

export function proxy(request: NextRequest): NextResponse {
  const nonce = newNonce();
  const policy = contentSecurityPolicy(nonce, { dev: process.env.NODE_ENV === "development" });

  // Next.js reads the nonce from the request's policy and puts it on its scripts.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers: requestHeaders } });

  response.headers.set("Content-Security-Policy", policy);
  const hsts = strictTransportSecurity(
    request.headers.get("x-forwarded-proto"),
    hstsMaxAgeSeconds(process.env.DAWAM_HSTS_MAX_AGE_SECONDS),
  );
  if (hsts !== null) {
    response.headers.set("Strict-Transport-Security", hsts);
  }
  return response;
}

export const config = {
  // Everything but the paths rewritten to FastAPI (BACKEND_PATHS), whose responses
  // carry the backend's own headers. Must be a literal: Next.js reads it at build.
  matcher: ["/((?!api(?:/|$)|healthz$|readyz$).*)"],
};
