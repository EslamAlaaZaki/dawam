"use client";

import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { Suspense, useState, type ReactNode } from "react";

import { AdminNav } from "../admin/AdminNav";
import { AUTH_PATHS, AuthFrame } from "../auth/AuthFrame";
import { Button } from "../components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetTrigger,
} from "../components/ui/sheet";
import { NotificationBell } from "./NotificationBell";
import { LocationTrail, SidebarNav } from "./SidebarNav";
import { UserMenu } from "./UserMenu";
import { JobsButton } from "../workspaces/JobsButton";
import { workspaceIdOf } from "../workspaces/WorkspaceSidebar";

const sidebarClasses =
  "flex flex-col gap-1 bg-surface px-3 py-3 text-base text-muted-foreground";

/**
 * The frame of every signed-in page: sidebar (240px; a drawer below 1024px), top bar
 * with the location trail, Jobs, notifications and the user menu, and the page. The sign-in pages have a
 * frame of their own instead (AuthFrame).
 */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const workspaceId = workspaceIdOf(pathname);
  if (AUTH_PATHS.has(pathname)) {
    return <AuthFrame>{children}</AuthFrame>;
  }
  return (
    <div className="min-h-screen bg-background scheme-light lg:grid lg:grid-cols-[240px_minmax(0,1fr)] dark:scheme-dark">
      <a
        href="#main"
        className="dawam-ui sr-only rounded-md bg-surface-raised px-3 py-2 font-medium text-foreground focus:not-sr-only focus:fixed focus:start-2 focus:top-2 focus:z-50 focus:shadow-overlay"
      >
        Skip to the page
      </a>
      <aside
        aria-label="Navigation"
        className={`dawam-ui ${sidebarClasses} sticky top-0 hidden h-screen overflow-y-auto border-e border-border lg:flex`}
      >
        <SidebarNav />
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="dawam-ui sticky top-0 z-10 flex h-12 items-center gap-3 border-b border-border bg-background ps-4 pe-3 lg:ps-6">
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
                if (
                  (event.target as HTMLElement).closest("a, [role=treeitem]")
                ) {
                  setDrawerOpen(false);
                }
              }}
            >
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <SidebarNav />
            </SheetContent>
          </Sheet>
          <Suspense>
            <LocationTrail />
          </Suspense>
          <div className="ms-auto flex shrink-0 items-center gap-1">
            {workspaceId && <JobsButton workspaceId={workspaceId} />}
            <NotificationBell />
            <UserMenu />
          </div>
        </header>
        <AdminNav />
        <main
          id="main"
          tabIndex={-1}
          className="dawam-ui flex flex-col gap-5 px-4 pt-5 pb-24 outline-none sm:px-6"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
