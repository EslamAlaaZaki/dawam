import type { NextConfig } from "next";

import { BACKEND_PATHS, STATIC_SECURITY_HEADERS } from "./src/security/headers";

// Where `web` forwards the API. Read when Next.js loads this file: on `next dev`, and
// on `next build`, which bakes the value into the build (the Docker image takes it as
// a build argument).
const apiUrl = (process.env.DAWAM_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

/** Every path except the backend's: the pages, assets and errors Next.js serves itself. */
const NEXT_SERVED = "/((?!api(?:/|$)|healthz$|readyz$).*)";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,

  // The browser talks only to `web`: the API and the probes are proxied to FastAPI
  // as they are (cookies included), so there is one origin and no CORS. `beforeFiles`
  // means no page or file can ever shadow them.
  async rewrites() {
    return {
      beforeFiles: BACKEND_PATHS.map((source) => ({
        source,
        destination: `${apiUrl}${source}`,
      })),
      afterFiles: [],
      fallback: [],
    };
  },

  // FastAPI sets its own headers on the API; these cover what Next.js serves. The
  // Content-Security-Policy (with a per-request nonce) and HSTS come from
  // `src/proxy.ts`.
  async headers() {
    return [{ source: NEXT_SERVED, headers: STATIC_SECURITY_HEADERS }];
  },
};

export default nextConfig;
