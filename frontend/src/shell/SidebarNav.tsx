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
  useWorkspaceTrail,
  workspaceIdOf,
} from "../workspaces/WorkspaceSidebar";

const linkClasses =
  "flex h-8 items-center gap-2 rounded-md px-2 whitespace-nowrap text-muted-foreground transition-colors duration-120 outline-primary hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:-outline-offset-2 aria-[current=page]:bg-primary-subtle aria-[current=page]:font-medium aria-[current=page]:text-foreground [&_svg]:size-4 [&_svg]:shrink-0 aria-[current=page]:[&_svg]:text-primary";

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

const labelClasses =
  "px-2 pt-4 pb-1 text-2xs font-medium tracking-wider text-subtle-foreground uppercase";

function Label({ children }: { children: string }) {
  return <p className={labelClasses}>{children}</p>;
}

/** Labels joined by "›": parents subtle, the last (current) level in the foreground. */
function Trail({ labels }: { labels: string[] }) {
  const parents = labels.slice(0, -1);
  return (
    <p className="flex min-w-0 items-center gap-1.5 text-sm whitespace-nowrap text-subtle-foreground">
      {parents.map((label, index) => (
        // Below 640px only the current level shows.
        <span key={index} className="hidden min-w-0 items-center gap-1.5 sm:flex">
          <span className="max-w-40 truncate">{label}</span>
          <span aria-hidden>›</span>
        </span>
      ))}
      <span className="min-w-0 truncate font-medium text-foreground">
        {labels.at(-1)}
      </span>
    </p>
  );
}

function WorkspaceTrail({ workspaceId }: { workspaceId: string }) {
  const labels = useWorkspaceTrail(workspaceId);
  return labels.length > 0 ? <Trail labels={labels} /> : null;
}

/**
 * The top bar's location trail: plain text built from labels the navigation already
 * shows (a Workspace's folder path, Admin and the page). It is not a link.
 */
export function LocationTrail() {
  const me = useMe();
  const pathname = usePathname();
  const workspaceId = workspaceIdOf(pathname);
  if (workspaceId) {
    return <WorkspaceTrail workspaceId={workspaceId} />;
  }
  const admin =
    me.data?.system_role === "admin" && !me.data.must_change_password;
  if (admin && pathname.startsWith("/admin")) {
    const page = ADMIN_GROUPS.flatMap((group) => group.links)
      .filter(
        (link) =>
          pathname === link.href || pathname.startsWith(`${link.href}/`),
      )
      .sort((a, b) => b.href.length - a.href.length)[0];
    return <Trail labels={page ? ["Admin", page.name] : ["Admin"]} />;
  }
  if (WORKSPACE_LIST_PATHS.has(pathname)) {
    return <Trail labels={["Workspaces"]} />;
  }
  return null;
}

/** The Workspaces list is current on the home page. */
const WORKSPACE_LIST_PATHS = new Set(["/"]);

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
      <Link
        href="/"
        aria-label="DAWAM home"
        className="rounded-md outline-offset-2 focus-visible:outline-2"
      >
        <Wordmark className="h-8 px-2 mb-2" />
      </Link>
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
            <p className="flex items-center gap-2 px-2 pt-4 text-sm font-semibold text-foreground">
              <ShieldCheck aria-hidden className="size-4 text-subtle-foreground" />
              Admin
            </p>
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
      <p className="mt-auto flex items-center border-t border-border px-2 pt-3 pb-1 text-xs text-subtle-foreground">
        <ApiStatus dot />
      </p>
    </>
  );
}
