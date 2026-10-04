// A stand-in for `next/navigation` in component tests, which run without Next.js:
// the current URL lives in memory, and the router changes it (see setup.ts).
import { useMemo, useSyncExternalStore } from "react";

const ORIGIN = "http://localhost:3000";

let href = `${ORIGIN}/`;
const listeners = new Set<() => void>();

/** Go to `path` (e.g. `/login?from=%2F`), as following a link would. */
export function setLocation(path: string): void {
  href = new URL(path, ORIGIN).href;
  for (const listener of listeners) {
    listener();
  }
}

/** The current path and query, e.g. `/login?from=%2F`; the whole URL once off-site. */
export function currentLocation(): string {
  const url = new URL(href);
  return url.origin === ORIGIN ? url.pathname + url.search : url.href;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function useHref(): string {
  return useSyncExternalStore(
    subscribe,
    () => href,
    () => href,
  );
}

export function usePathname(): string {
  return new URL(useHref()).pathname;
}

export function useSearchParams(): URLSearchParams {
  const current = useHref();
  return useMemo(() => new URL(current).searchParams, [current]);
}

const router = {
  push: setLocation,
  replace: setLocation,
  back: () => {},
  forward: () => {},
  refresh: () => {},
  prefetch: () => {},
};

export function useRouter() {
  return router;
}
