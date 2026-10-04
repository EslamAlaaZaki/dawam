"use client";

import { useMe } from "../api/queries";

/** Links to the admin pages, shown to admins only. */
export function AdminNav() {
  const me = useMe();
  if (me.data?.system_role !== "admin") {
    return null;
  }
  return (
    <nav className="shell-nav" aria-label="Administration">
      <a href="/admin/email">Email settings</a>
      <a href="/admin/email/links">Links to share</a>
    </nav>
  );
}
