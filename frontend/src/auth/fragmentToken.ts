import { useSyncExternalStore } from "react";

// One-time links carry their token in the fragment (`/reset-password#token=…`), which
// the browser never sends to a server, so it stays out of proxy logs and Referer headers.
function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function tokenFromHash(): string {
  return new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
}

/** The token in the page's fragment (`""` if none); `null` while rendering on the server. */
export function useFragmentToken(): string | null {
  return useSyncExternalStore(subscribe, tokenFromHash, () => null);
}
