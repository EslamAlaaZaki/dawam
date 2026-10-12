"use client";

import { useMe } from "../api/queries";
import { LlmSetupBanner } from "./LlmSetupBanner";

/** The admin banners above a page's content, shown to admins only (the links are in the sidebar). */
export function AdminNav() {
  const me = useMe();
  if (me.data?.system_role !== "admin" || me.data.must_change_password) {
    return null;
  }
  return <LlmSetupBanner />;
}
