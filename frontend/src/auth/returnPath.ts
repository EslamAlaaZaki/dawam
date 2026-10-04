// Where to go after signing in travels in the login page's URL: `/login?from=/a/b`.

/** The login page, remembering `returnTo` (a path on this site) for after signing in. */
export function loginPath(returnTo: string): string {
  return returnTo === "/" ? "/login" : `/login?from=${encodeURIComponent(returnTo)}`;
}

/**
 * The page to go to after signing in: `from` when it is a page of this site, else `/`.
 * Anything that would leave the origin (`https://…`, `//host`, `/\host`, …) is refused,
 * so the login page can never be used to send someone elsewhere.
 */
export function safeReturnPath(from: string | null, origin = window.location.origin): string {
  if (from === null || !from.startsWith("/")) {
    return "/";
  }
  let url: URL;
  try {
    url = new URL(from, origin);
  } catch {
    return "/";
  }
  if (url.origin !== origin || url.pathname === "/login") {
    return "/";
  }
  return url.pathname + url.search + url.hash;
}
