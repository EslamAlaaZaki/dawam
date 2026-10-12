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
import { JobsButton } from "../workspaces/JobsButton";
import { workspaceIdOf } from "../workspaces/WorkspaceSidebar";

const sidebarClasses = "flex flex-col gap-1 bg-sand-2 px-2 py-3 text-base";

/**
 * The frame of every signed-in page: sidebar (a drawer below 1024px), top bar with
 * breadcrumbs, notifications and the user menu, and the page. The sign-in pages have a
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
    <div className="min-h-screen bg-sand-1 scheme-light lg:grid lg:grid-cols-[248px_minmax(0,1fr)] dark:scheme-dark">
      <a
        href="#main"
        className="dawam-ui sr-only rounded-md bg-sand-1 px-3 py-2 font-medium text-sand-12 focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:shadow-overlay"
      >
        Skip to the page
      </a>
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
          <Breadcrumbs />
          <div className="ml-auto flex items-center gap-1">
            {workspaceId && <JobsButton workspaceId={workspaceId} />}
            <NotificationBell />
            <UserMenu />
          </div>
        </header>
        <AdminNav />
        <main
          id="main"
          tabIndex={-1}
          className="dawam-ui flex flex-col gap-5 px-6 pt-5 pb-24 outline-none"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
