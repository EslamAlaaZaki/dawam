# The frontend is a Next.js app with its own server

The frontend is built with Next.js (App Router, TypeScript) and runs as its own `web` service, instead of a Vite single-page app whose built files the FastAPI `app` serves. Next.js gives the frontend a framework with file-based routing, layouts, per-route code splitting and server rendering when a screen benefits from it. We chose it knowing DAWAM needs none of its SEO features, because every screen sits behind sign-in.

## How it fits

- **One origin for the browser.** The browser only talks to `edge`, an nginx proxy in front of both servers. It sends `/api/` (and `/healthz`, `/readyz`) to the FastAPI `app` service and everything else to Next.js (`web`), so the session cookie, the double-submit CSRF cookie and `SameSite=Lax` behave as if there were one server. FastAPI stays the only place that authenticates, authorizes and stores data. No business logic, sessions or database access live in Next.js.
- **`edge` is the single trusted hop.** It overwrites `X-Forwarded-For` and `X-Forwarded-Proto` (believing a TLS proxy in front only when told its addresses), and FastAPI trusts those headers from `edge`'s address alone. We put nginx in front instead of having Next.js rewrite `/api/*` to FastAPI because Next.js passes a client's forwarded headers on as they came, so FastAPI could not trust them from `web` without trusting whatever a client claims; because API responses that stream (server-sent events for the assistant) need a proxy that does not buffer and allows long reads; and because Next.js bakes rewrite targets into its build, while `edge` finds `app` at run time.
- **Data fetching stays in the browser by default.** Screens are client components that call the generated OpenAPI client through TanStack Query. Server components are used for layout and static shell only. Moving a screen to server-side fetching is allowed later, but it must forward the user's cookies to FastAPI and never hold its own session.
- **Security headers on both servers.** FastAPI keeps its headers on API responses. Next.js sets the same baseline (CSP with `frame-ancestors 'none'`, `nosniff`, referrer policy, HSTS behind TLS) on the pages it serves.
- **Packaging.** Compose gains a `web` service built with Next.js `output: "standalone"` and an `edge` nginx service, the only one published on the host; `app` no longer serves frontend files.

## Considered options

- **Vite single-page app served by FastAPI** (the original choice): one image, one process and one place for headers. It was set aside for Next.js's routing, layouts and framework conventions, at the cost of a second runtime to build, patch and run.
- **Next.js static export served by FastAPI**: keeps one server but drops most of what Next.js adds (server rendering, middleware, dynamic routes without pre-generated params), so it gains little over Vite.
- **Next.js calling FastAPI from a different origin**: rejected. It needs CORS with credentials and cross-site cookies, which weakens `SameSite` and the CSRF design.
