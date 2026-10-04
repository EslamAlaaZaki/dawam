// @vitest-environment node
// The pages `web` serves carry the spec's security baseline (§8.4), and the API, the
// probes and their cookies are proxied to FastAPI untouched.
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../../next.config";
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

  it("is sent when a TLS-terminating proxy forwarded the request", () => {
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

  it("reads the client-facing hop of a forwarded chain", () => {
    expect(strictTransportSecurity("https, http", 60)).toBe("max-age=60");
    expect(strictTransportSecurity("http, https", 60)).toBeNull();
  });

  it("refuses a max-age that is not whole seconds", () => {
    expect(hstsMaxAgeSeconds(undefined)).toBe(31_536_000);
    expect(hstsMaxAgeSeconds("")).toBe(31_536_000);
    expect(() => hstsMaxAgeSeconds("a year")).toThrow("DAWAM_HSTS_MAX_AGE_SECONDS");
    expect(() => hstsMaxAgeSeconds("-1")).toThrow("DAWAM_HSTS_MAX_AGE_SECONDS");
  });
});

describe("next.config", () => {
  it("builds a standalone server", () => {
    expect(nextConfig.output).toBe("standalone");
  });

  it("sends nosniff and the referrer policy on everything it serves, not on the API", async () => {
    const rules = await nextConfig.headers!();

    expect(rules).toHaveLength(1);
    const [rule] = rules;
    expect(rule!.headers).toEqual([
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "same-origin" },
    ]);
    // The same exclusion as the proxy's: FastAPI owns the headers of what it serves.
    expect(rule!.source).toBe(proxyConfig.matcher[0]);
    const served = new RegExp(`^${rule!.source}$`);
    for (const path of ["/", "/login", "/workspaces/42", "/_next/static/chunks/a.js", "/apiary"]) {
      expect(served.test(path), path).toBe(true);
    }
    for (const path of ["/api", "/api/v1/version", "/healthz", "/readyz"]) {
      expect(served.test(path), path).toBe(false);
    }
  });

  it("forwards the API and the probes to FastAPI before any page or file", async () => {
    const rewrites = await nextConfig.rewrites!();

    expect(rewrites).toEqual({
      beforeFiles: [
        { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
        { source: "/healthz", destination: "http://localhost:8000/healthz" },
        { source: "/readyz", destination: "http://localhost:8000/readyz" },
      ],
      afterFiles: [],
      fallback: [],
    });
  });
});
