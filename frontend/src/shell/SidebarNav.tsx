"use client";

import {
  Cpu,
  Folders,
  Link2,
  Mail,
  MailPlus,
  Settings,
  Shield,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useMe } from "../api/queries";
import { cn } from "../lib/utils";
import { ApiStatus } from "./ApiStatus";
import { Wordmark } from "../components/Wordmark";

const linkClasses =
  "flex items-center gap-2 rounded-md px-2 py-[5px] whitespace-nowrap text-sand-11 hover:bg-sand-3 hover:text-sand-12 aria-[current=page]:bg-sand-4 aria-[current=page]:font-medium aria-[current=page]:text-sand-12 [&_svg]:size-4 [&_svg]:shrink-0";

function NavLink({
  href,
  icon: Icon,
  current,
  children,
}: {
  href: string;
  icon: LucideIcon;
  current: boolean;
  children: string;
}) {
  return (
    <Link href={href} className={linkClasses} aria-current={current ? "page" : undefined}>
      <Icon aria-hidden />
      {children}
    </Link>
  );
}

function Label({ children }: { children: string }) {
  return (
    <p className="px-2 pt-3.5 pb-1 text-2xs font-medium tracking-[0.06em] text-sand-11 uppercase">
      {children}
    </p>
  );
}

/** The Workspaces list is current on the home page and while creating one. */
const WORKSPACE_LIST_PATHS = new Set(["/", "/workspaces/new"]);

/**
 * The sidebar's content (prototype "Workspace" and "Admin" screens), shared by the fixed
 * sidebar and the drawer. Outside a Workspace: the Workspaces list, and for admins the
 * Admin links. The in-Workspace navigation (switcher and folders) is a later ticket and
 * replaces the middle section for /workspaces/<id> pages.
 */
export function SidebarNav() {
  const me = useMe();
  const pathname = usePathname();
  const admin = me.data?.system_role === "admin" && !me.data.must_change_password;
  const here = (href: string) => pathname === href;
  return (
    <>
      <Wordmark className="px-2 pt-1 pb-3" />
      <nav aria-label="Workspaces" className="flex flex-col gap-0.5">
        <NavLink href="/" icon={Folders} current={WORKSPACE_LIST_PATHS.has(pathname)}>
          Workspaces
        </NavLink>
      </nav>
      {admin && (
        <nav aria-label="Administration" className="flex flex-col gap-0.5">
          <Label>People</Label>
          <NavLink href="/admin/users" icon={Users} current={here("/admin/users")}>
            Users
          </NavLink>
          <NavLink href="/admin/invitations" icon={MailPlus} current={here("/admin/invitations")}>
            Invitations
          </NavLink>
          <NavLink href="/admin/workspaces" icon={Folders} current={here("/admin/workspaces")}>
            Workspaces
          </NavLink>
          <Label>Security</Label>
          <NavLink
            href="/admin/security-events"
            icon={Shield}
            current={here("/admin/security-events")}
          >
            Security events
          </NavLink>
          <NavLink href="/admin/settings" icon={Settings} current={here("/admin/settings")}>
            Admin settings
          </NavLink>
          <Label>Email</Label>
          <NavLink href="/admin/email" icon={Mail} current={here("/admin/email")}>
            Email settings
          </NavLink>
          <NavLink href="/admin/email/links" icon={Link2} current={here("/admin/email/links")}>
            Links to share
          </NavLink>
          <Label>AI</Label>
          <NavLink href="/admin/llm" icon={Cpu} current={here("/admin/llm")}>
            Language models
          </NavLink>
          <NavLink href="/admin/llm/settings" icon={Wallet} current={here("/admin/llm/settings")}>
            AI roles, budgets &amp; usage
          </NavLink>
        </nav>
      )}
      <p className={cn("mt-auto flex items-center p-2 text-xs text-sand-11")}>
        <ApiStatus dot />
      </p>
    </>
  );
}
