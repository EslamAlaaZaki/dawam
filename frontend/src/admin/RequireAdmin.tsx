"use client";

import type { ReactNode } from "react";

import { useMe } from "../api/queries";
import { ErrorText } from "./parts";

/**
 * Renders `children` for an admin. Use inside the signed-in layout. The API checks
 * every request anyway; this only spares other users a page they cannot use.
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe();
  if (me.data?.system_role !== "admin") {
    return (
      <ErrorText>Only an administrator can see this page.</ErrorText>
    );
  }
  return children;
}
