// @vitest-environment node
// The pages `web` serves carry the spec's security baseline (§8.4). In production the
// API never reaches `web` (the `edge` proxy routes it to FastAPI); only `next dev`
// forwards it.
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD, PHASE_PRODUCTION_SERVER } from "next/constants";
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfigFor from "../../next.config";
import { config as proxyConfig, proxy } from "../proxy";
import { contentSecurityPolicy, hstsMaxAgeSeconds, strictTransportSecurity } from "./headers";

function directivesOf(policy: string): Record<string, string[]> {
  const directives: Record<string, string[]> = {};
  for (const part of policy.split(";")) {
    const [name, ...sources] = part.trim().split(/\s+/);
    directives[name!] = sources;
  }
  return directives;
}

function pageRequest(path: string, headers: Record<string, string> = {}) {
  return new NextRequest(new URL(path, "http://dawam.test"), { headers });
}

/** The value of a request header as Next.js passes it on to the page renderer. */
function forwardedRequestHeader(response: Response, name: string): string | null {
  return response.headers.get(`x-middleware-request-${name.toLowerCase()}`);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("the Content-Security-Policy of a page", () => {
  it("allows only the site's own code and is never framed", () => {
    const policy = directivesOf(proxy(pageRequest("/login")).headers.get("Content-Security-Policy")!);

    expect(policy["frame-ancestors"]).toEqual(["'none'"]);
    expect(policy["default-src"]).toEqual(["'self'"]);
    expect(policy["object-src"]).toEqual(["'none'"]);
    expect(policy["base-uri"]).toEqual(["'self'"]);
    expect(policy["form-action"]).toEqual(["'self'"]);
    expect(policy["img-src"]).toEqual(["'self'", "data:"]);
    expect(policy["script-src"]).toEqual(["'self'", expect.stringMatching(/^'nonce-[A-Za-z0-9+/]{22}=='$/)]);
    expect(policy["style-src"]).toEqual(["'self'", policy["script-src"]![1]]);
  });

  it("has no unsafe sources and no other origins in production", () => {
    const policy = proxy(pageRequest("/")).headers.get("Content-Security-Policy")!;

    expect(policy).not.toContain("unsafe");
    expect(policy).not.toContain("http");
  });

  it("uses a fresh nonce on every request, and hands it to Next.js's renderer", () => {
    const first = proxy(pageRequest("/"));
    const second = proxy(pageRequest("/"));

    const firstPolicy = first.headers.get("Content-Security-Policy");
    expect(firstPolicy).not.toEqual(second.headers.get("Content-Security-Policy"));
    expect(forwardedRequestHeader(first, "Content-Security-Policy")).toEqual(firstPolicy);
  });

  it("relaxes only what `next dev` needs, and only in development", () => {
    const dev = directivesOf(contentSecurityPolicy("abc", { dev: true }));

    expect(dev["script-src"]).toEqual(["'self'", "'nonce-abc'", "'unsafe-eval'"]);
    expect(dev["style-src"]).toEqual(["'self'", "'unsafe-inline'"]);
    expect(dev["frame-ancestors"]).toEqual(["'none'"]);
  });
});

describe("HSTS on pages", () => {
  it("is not sent over plain HTTP", () => {
    expect(proxy(pageRequest("/")).headers.get("Strict-Transport-Security")).toBeNull();
    expect(
      proxy(pageRequest("/", { "X-Forwarded-Proto": "http" })).headers.get(
        "Strict-Transport-Security",
      ),
    ).toBeNull();
  });

  it("is sent when edge says the client used HTTPS", () => {
    const response = proxy(pageRequest("/", { "X-Forwarded-Proto": "https" }));

    expect(response.headers.get("Strict-Transport-Security")).toBe("max-age=31536000");
  });

  it("follows DAWAM_HSTS_MAX_AGE_SECONDS, where 0 turns it off", () => {
    vi.stubEnv("DAWAM_HSTS_MAX_AGE_SECONDS", "600");
    const https = { "X-Forwarded-Proto": "https" };
    expect(proxy(pageRequest("/", https)).headers.get("Strict-Transport-Security")).toBe(
      "max-age=600",
    );

    vi.stubEnv("DAWAM_HSTS_MAX_AGE_SECONDS", "0");
    expect(proxy(pageRequest("/", https)).headers.get("Strict-Transport-Security")).toBeNull();
  });

  it("takes edge's X-Forwarded-Proto as it is: one value, which it always sets", () => {
    expect(strictTransportSecurity("https", 60)).toBe("max-age=60");
    expect(strictTransportSecurity("http, https", 60)).toBeNull();
    expect(strictTransportSecurity(null, 60)).toBeNull();
  });

  it("refuses a max-age that is not whole seconds", () => {
    expect(hstsMaxAgeSeconds(undefined)).toBe(31_536_000);
    expect(hstsMaxAgeSeconds("")).toBe(31_536_000);
    expect(() => hstsMaxAgeSeconds("a year")).toThrow("DAWAM_HSTS_MAX_AGE_SECONDS");
    expect(() => hstsMaxAgeSeconds("-1")).toThrow("DAWAM_HSTS_MAX_AGE_SECONDS");
  });
});

describe("the proxy's matcher", () => {
  const [matcher] = proxyConfig.matcher;
  const matches = (path: string) => new RegExp(`^${matcher!.source}$`).test(path);

  it("covers the pages", () => {
    for (const path of ["/", "/login", "/workspaces/42", "/apiary"]) {
      expect(matches(path), path).toBe(true);
    }
  });

  it("skips static files, images, the favicon, prefetches and the API", () => {
    for (const path of [
      "/_next/static/chunks/a.js",
      "/_next/image",
      "/favicon.ico",
      "/api/v1/version",
    ]) {
      expect(matches(path), path).toBe(false);
    }
    expect(matcher!.missing).toEqual([
      { type: "header", key: "next-router-prefetch" },
      { type: "header", key: "purpose", value: "prefetch" },
    ]);
  });
});

describe("next.config", () => {
  const production = nextConfigFor(PHASE_PRODUCTION_BUILD);

  it("builds a standalone server", () => {
    expect(production.output).toBe("standalone");
  });

  it("sends nosniff and the referrer policy on everything it serves", async () => {
    expect(await production.headers!()).toEqual([
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ]);
  });

  it("never forwards the API in production: edge routes it to FastAPI", () => {
    expect(production.rewrites).toBeUndefined();
    expect(nextConfigFor(PHASE_PRODUCTION_SERVER).rewrites).toBeUndefined();
  });

  it("forwards the API and the probes under `next dev`, before any page or file", async () => {
    vi.stubEnv("DAWAM_DEV_API_URL", "http://127.0.0.1:9000/");
    const rewrites = await nextConfigFor(PHASE_DEVELOPMENT_SERVER).rewrites!();

    expect(rewrites).toEqual({
      beforeFiles: [
        { source: "/api/:path*", destination: "http://127.0.0.1:9000/api/:path*" },
        { source: "/healthz", destination: "http://127.0.0.1:9000/healthz" },
        { source: "/readyz", destination: "http://127.0.0.1:9000/readyz" },
      ],
      afterFiles: [],
      fallback: [],
    });
  });
});
