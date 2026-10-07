"use client";

import { useMe } from "../api/queries";
import { LlmSetupBanner } from "./LlmSetupBanner";

/** Links to the admin pages, shown to admins only. */
export function AdminNav() {
  const me = useMe();
  if (me.data?.system_role !== "admin" || me.data.must_change_password) {
    return null;
  }
  return (
    <>
      <nav className="shell-nav" aria-label="Administration">
        <a href="/admin/users">Users</a>
        <a href="/admin/workspaces">Workspaces</a>
        <a href="/admin/invitations">Invitations</a>
        <a href="/admin/security-events">Security events</a>
        <a href="/admin/email">Email settings</a>
        <a href="/admin/email/links">Links to share</a>
        <a href="/admin/llm">Language models</a>
        <a href="/admin/llm/settings">AI roles, budgets &amp; usage</a>
      </nav>
      <LlmSetupBanner />
    </>
  );
}
