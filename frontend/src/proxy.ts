// Runs before every page request `web` serves (see `config.matcher`) and adds the
// per-request security headers.
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
  // Pages only, as Next.js recommends: not its static files and images (which get
  // nosniff from next.config's headers()), not prefetches, and not the API, which
  // only reaches `web` under `next dev` and keeps FastAPI's own headers. Must be a
  // literal: Next.js reads it at build.
  matcher: [
    {
      source: "/((?!api/|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
