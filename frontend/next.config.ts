import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

import { STATIC_SECURITY_HEADERS } from "./src/security/headers";

/** What FastAPI serves; in production the `edge` proxy sends these paths to `app`. */
const BACKEND_PATHS = ["/api/:path*", "/healthz", "/readyz"];

export default function nextConfig(phase: string): NextConfig {
  const config: NextConfig = {
    output: "standalone",
    poweredByHeader: false,
    reactStrictMode: true,

    // The Content-Security-Policy (with a per-request nonce) and HSTS come from
    // src/proxy.ts.
    async headers() {
      return [{ source: "/:path*", headers: STATIC_SECURITY_HEADERS }];
    },
  };
  // `edge` puts `web` and `app` behind one origin. `next dev` runs without it, so
  // only there does Next.js forward the API itself (to DAWAM_DEV_API_URL).
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    config.rewrites = async () => devRewrites(process.env.DAWAM_DEV_API_URL);
  }
  return config;
}

function devRewrites(apiUrl = "http://localhost:8000") {
  const origin = apiUrl.replace(/\/+$/, "");
  return {
    beforeFiles: BACKEND_PATHS.map((source) => ({ source, destination: `${origin}${source}` })),
    afterFiles: [],
    fallback: [],
  };
}
