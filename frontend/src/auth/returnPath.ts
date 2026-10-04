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
  // Check the path as the URL parser normalised it: `/.//host` and `/a/..//host` only
  // become `//host` (another site, to the router) after dot segments are removed. The
  // decoded form is checked too, so `/%2F%2Fhost` fails wherever it gets decoded.
  if (url.origin !== origin || leavesTheSite(url.pathname) || url.pathname === "/login") {
    return "/";
  }
  return url.pathname + url.search + url.hash;
}

function leavesTheSite(pathname: string): boolean {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return true;
  }
  return [pathname, decoded].some((path) => /^\/[/\\]/.test(path) || /^\/\.+[/\\]/.test(path));
}
