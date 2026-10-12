"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { AdminNav } from "../admin/AdminNav";
import { AUTH_PATHS, AuthFrame } from "../auth/AuthFrame";
import { ApiStatus } from "./ApiStatus";
import { NotificationBell } from "./NotificationBell";
import { SignedInUser } from "./SignedInUser";

/**
 * The frame of every page: header (with notifications and the signed-in user), content,
 * API status. The sign-in pages have a frame of their own instead (AuthFrame).
 */
export function Shell({ children }: { children: ReactNode }) {
  if (AUTH_PATHS.has(usePathname())) {
    return <AuthFrame>{children}</AuthFrame>;
  }
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>DAWAM</h1>
        <p>Data Analysis &amp; Warehouse Architecture Modeler</p>
        <AdminNav />
        <NotificationBell />
        <SignedInUser />
      </header>
      <main className="shell-main">{children}</main>
      <footer className="shell-footer">
        <ApiStatus />
      </footer>
    </div>
  );
}
