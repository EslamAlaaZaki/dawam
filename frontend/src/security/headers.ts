// The security headers of the spec's baseline (§8.4) for everything `web` serves.
// The API never passes through `web`: the `edge` proxy sends it straight to FastAPI,
// which sets the same baseline (backend/src/dawam/platform/security_headers.py).

/** Headers that never change, set by `headers()` in next.config.ts. */
export const STATIC_SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  // No referrer ever leaves the site, while same-origin requests keep it.
  { key: "Referrer-Policy", value: "same-origin" },
];

/**
 * The Content-Security-Policy for one page, set by `src/proxy.ts`.
 *
 * Next.js puts inline scripts in every page (the React Server Components payload and
 * its bootstrap), so `'self'` alone would break the app. Instead each request gets a
 * fresh nonce: Next.js reads it from this header and adds it to the scripts it
 * renders, and nothing else inline may run. That needs every page rendered per
 * request (see `connection()` in the root layout).
 *
 * `next dev` also needs `'unsafe-eval'` (React rebuilds server error stacks with
 * `eval`) and `'unsafe-inline'` styles (it injects CSS as `<style>` tags without a
 * nonce). Neither is ever in the policy of a production build.
 */
export function contentSecurityPolicy(nonce: string, options: { dev: boolean }): string {
  const { dev } = options;
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' ${dev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** A fresh, unguessable nonce (128 random bits, base64). */
export function newNonce(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return btoa(String.fromCharCode(...bytes));
}

/**
 * The `Strict-Transport-Security` value for a request, or `null` when none is sent.
 *
 * `web` speaks plain HTTP and is reachable only through `edge`, which always
 * overwrites `X-Forwarded-Proto` with the scheme the client used (believing a TLS
 * proxy in front only when DAWAM_TRUSTED_PROXY_CIDRS names it). `maxAgeSeconds` is
 * `DAWAM_HSTS_MAX_AGE_SECONDS`; 0 turns HSTS off.
 */
export function strictTransportSecurity(
  forwardedProto: string | null,
  maxAgeSeconds: number,
): string | null {
  if (forwardedProto !== "https" || !(maxAgeSeconds > 0)) {
    return null;
  }
  return `max-age=${Math.floor(maxAgeSeconds)}`;
}

/** `DAWAM_HSTS_MAX_AGE_SECONDS`: whole seconds, with the backend's default of one year. */
export function hstsMaxAgeSeconds(value: string | undefined): number {
  if (value === undefined || value.trim() === "") {
    return 31_536_000;
  }
  if (!/^\s*\d+\s*$/.test(value)) {
    throw new Error(`DAWAM_HSTS_MAX_AGE_SECONDS must be a whole number of seconds, not ${value}`);
  }
  return Number(value);
}
