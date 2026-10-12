"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { AdminNav } from "../admin/AdminNav";
import { AUTH_PATHS, AuthFrame } from "../auth/AuthFrame";
import { Button } from "../components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "../components/ui/sheet";
import { Breadcrumbs } from "./Breadcrumbs";
import { NotificationBell } from "./NotificationBell";
import { SidebarNav } from "./SidebarNav";
import { UserMenu } from "./UserMenu";

/** Pages in the redesign's look; the others stay light-only until their ticket lands. */
const REDESIGNED_PATHS: ReadonlySet<string> = new Set([
  "/",
  "/workspaces/new",
  "/profile",
  "/admin/users",
  "/admin/users/new",
  "/admin/invitations",
  "/admin/workspaces",
]);

const sidebarClasses = "flex flex-col gap-1 bg-sand-2 px-2 py-3 text-base";

/**
 * The frame of every signed-in page: sidebar (a drawer below 1024px), top bar with
 * breadcrumbs, notifications and the user menu, and the page. The sign-in pages have a
 * frame of their own instead (AuthFrame).
 */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  if (AUTH_PATHS.has(pathname)) {
    return <AuthFrame>{children}</AuthFrame>;
  }
  return (
    <div className="min-h-screen bg-sand-1 scheme-light lg:grid lg:grid-cols-[248px_minmax(0,1fr)] dark:scheme-dark">
      <aside
        aria-label="Navigation"
        className={`dawam-ui ${sidebarClasses} sticky top-0 hidden h-screen overflow-y-auto border-r border-line lg:flex`}
      >
        <SidebarNav />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="dawam-ui sticky top-0 z-10 flex h-12 items-center gap-3 border-b border-line bg-sand-1 px-4">
          <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="lg:hidden"
                aria-label="Open navigation"
              >
                <Menu aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent
              className={sidebarClasses}
              aria-describedby={undefined}
              // Following a link in the drawer closes it.
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a")) {
                  setDrawerOpen(false);
                }
              }}
            >
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <SidebarNav />
            </SheetContent>
          </Sheet>
          <Breadcrumbs />
          <div className="ml-auto flex items-center gap-1">
            {/* The Jobs button comes here (a later ticket). */}
            <NotificationBell />
            <UserMenu />
          </div>
        </header>
        <AdminNav />
        <main
          className={
            REDESIGNED_PATHS.has(pathname)
              ? "dawam-ui flex flex-col gap-5 px-6 pt-5 pb-24"
              : "legacy-page flex-1"
          }
        >
          {children}
        </main>
      </div>
    </div>
  );
}
