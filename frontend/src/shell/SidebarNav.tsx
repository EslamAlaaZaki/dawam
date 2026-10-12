"use client";

import {
  ArrowLeft,
  Cpu,
  Folders,
  Link2,
  Mail,
  MailPlus,
  Settings,
  Shield,
  ShieldCheck,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment, Suspense } from "react";

import { useMe } from "../api/queries";
import { ApiStatus } from "./ApiStatus";
import { Wordmark } from "../components/Wordmark";
import {
  WorkspaceSidebar,
  workspaceIdOf,
} from "../workspaces/WorkspaceSidebar";

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
    <Link
      href={href}
      className={linkClasses}
      aria-current={current ? "page" : undefined}
    >
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

/** The Admin sidebar's groups (prototype "Admin" screen). */
const ADMIN_GROUPS: {
  label: string;
  links: { href: string; icon: LucideIcon; name: string }[];
}[] = [
  {
    label: "People",
    links: [
      { href: "/admin/users", icon: Users, name: "Users" },
      { href: "/admin/invitations", icon: MailPlus, name: "Invitations" },
      { href: "/admin/workspaces", icon: Folders, name: "Workspaces" },
    ],
  },
  {
    label: "Security",
    links: [
      { href: "/admin/security-events", icon: Shield, name: "Security events" },
      { href: "/admin/settings", icon: Settings, name: "Admin settings" },
    ],
  },
  {
    label: "Email",
    links: [
      { href: "/admin/email", icon: Mail, name: "Settings" },
      { href: "/admin/email/links", icon: Link2, name: "Links to share" },
    ],
  },
  {
    label: "AI",
    links: [
      { href: "/admin/llm", icon: Cpu, name: "Language models" },
      {
        href: "/admin/llm/settings",
        icon: Wallet,
        name: "Roles, budgets & usage",
      },
    ],
  },
];

/**
 * The sidebar's content (prototype "Workspace" and "Admin" screens), shared by the fixed
 * sidebar and the drawer. Under /admin, admins get the grouped Admin navigation and a way
 * back; inside a Workspace the Workspace switcher and its folder tree; elsewhere the
 * Workspaces list, and for admins one Admin entry.
 */
export function SidebarNav() {
  const me = useMe();
  const pathname = usePathname();
  const admin =
    me.data?.system_role === "admin" && !me.data.must_change_password;
  const inAdmin = admin && pathname.startsWith("/admin");
  const workspaceId = workspaceIdOf(pathname);
  return (
    <>
      <Wordmark className="px-2 pt-1 pb-3" />
      {workspaceId ? (
        // The tree reads the `folder` search parameter, which wants a Suspense boundary.
        <Suspense>
          <WorkspaceSidebar workspaceId={workspaceId} />
        </Suspense>
      ) : inAdmin ? (
        <>
          <nav aria-label="Workspaces" className="flex flex-col gap-0.5">
            <NavLink href="/" icon={ArrowLeft} current={false}>
              Back to Workspaces
            </NavLink>
          </nav>
          <nav aria-label="Administration" className="flex flex-col gap-0.5">
            {ADMIN_GROUPS.map((group) => (
              <Fragment key={group.label}>
                <Label>{group.label}</Label>
                {group.links.map((link) => (
                  <NavLink
                    key={link.href}
                    href={link.href}
                    icon={link.icon}
                    current={pathname === link.href}
                  >
                    {link.name}
                  </NavLink>
                ))}
              </Fragment>
            ))}
          </nav>
        </>
      ) : (
        <>
          <nav aria-label="Workspaces" className="flex flex-col gap-0.5">
            <NavLink
              href="/"
              icon={Folders}
              current={WORKSPACE_LIST_PATHS.has(pathname)}
            >
              Workspaces
            </NavLink>
          </nav>
          {admin && (
            <nav aria-label="Administration" className="flex flex-col gap-0.5">
              <NavLink href="/admin/users" icon={ShieldCheck} current={false}>
                Admin
              </NavLink>
            </nav>
          )}
        </>
      )}
      <p className="mt-auto flex items-center p-2 text-xs text-sand-11">
        <ApiStatus dot />
      </p>
    </>
  );
}
