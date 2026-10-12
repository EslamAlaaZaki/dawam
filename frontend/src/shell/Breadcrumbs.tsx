"use client";

import { usePathname } from "next/navigation";
import { Fragment } from "react";

const ADMIN_PAGES: Record<string, [section: string, page: string]> = {
  "/admin/users": ["People", "Users"],
  "/admin/users/new": ["People", "Create user"],
  "/admin/invitations": ["People", "Invitations"],
  "/admin/workspaces": ["People", "Workspaces"],
  "/admin/security-events": ["Security", "Security events"],
  "/admin/settings": ["Security", "Admin settings"],
  "/admin/email": ["Email", "Settings"],
  "/admin/email/links": ["Email", "Links to share"],
  "/admin/llm": ["AI", "Language models"],
  "/admin/llm/settings": ["AI", "Roles, budgets & usage"],
};

/** The trail for a path: where the page sits, the last item being the page itself. */
export function crumbsFor(pathname: string): string[] {
  if (pathname === "/") {
    return ["Workspaces"];
  }
  if (pathname === "/workspaces/new") {
    return ["Workspaces", "New Workspace"];
  }
  if (pathname === "/profile") {
    return ["Account", "Profile"];
  }
  if (pathname.startsWith("/admin")) {
    return ["Admin", ...(ADMIN_PAGES[pathname] ?? [])];
  }
  if (pathname.startsWith("/workspaces/")) {
    return ["Workspaces", "Workspace"];
  }
  return ["DAWAM"];
}

export function Breadcrumbs() {
  const crumbs = crumbsFor(usePathname());
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap text-sand-11"
    >
      {crumbs.map((crumb, index) => (
        <Fragment key={crumb}>
          {index === crumbs.length - 1 ? (
            <b aria-current="page" className="truncate font-medium text-sand-12">
              {crumb}
            </b>
          ) : (
            <>
              <span>{crumb}</span>
              <span aria-hidden className="text-sand-8">
                /
              </span>
            </>
          )}
        </Fragment>
      ))}
    </nav>
  );
}
