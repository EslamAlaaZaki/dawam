"use client";

import { ChevronsUpDown } from "lucide-react";
import Link from "next/link";

import { useWorkspace, useWorkspaces } from "../api/workspaces";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { findFolder } from "./folders";
import { FolderTree } from "./FolderTree";
import { useWorkspaceFolders } from "./useWorkspaceFolders";
import { Loading } from "../shell/Loading";

/** The Workspace's id when `pathname` is a Workspace's page (not "new", not a sub-page). */
export function workspaceIdOf(pathname: string): string | null {
  const id = /^\/workspaces\/([^/]+)$/.exec(pathname)?.[1];
  return id && id !== "new" ? decodeURIComponent(id) : null;
}

/**
 * The labels from the Workspace down to its selected folder, for the top bar's location
 * trail (empty until the Workspace has loaded).
 */
export function useWorkspaceTrail(workspaceId: string): string[] {
  const workspace = useWorkspace(workspaceId);
  const { root, folder } = useWorkspaceFolders(
    workspaceId,
    workspace.data?.name ?? "",
  );
  if (!workspace.data) {
    return [];
  }
  const parts = folder.id === "" ? [] : folder.id.split("/");
  return [
    root.label,
    ...parts.map(
      (_, index) =>
        findFolder(root, parts.slice(0, index + 1).join("/"))?.label ?? "",
    ),
  ].filter(Boolean);
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]!.toUpperCase())
    .join("");
}

/** The Workspaces to switch to; fetched only once the switcher is open. */
function SwitcherList({ currentId }: { currentId: string }) {
  const workspaces = useWorkspaces();
  const items = workspaces.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      {workspaces.isPending && (
        <Loading className="px-2 py-1" />
      )}
      {workspaces.isError && (
        <p role="alert" className="px-2 py-1">
          Could not load the Workspaces.
        </p>
      )}
      <ul className="flex flex-col gap-0.5">
        {items.map((workspace) => (
          <li key={workspace.id}>
            <Link
              href={`/workspaces/${workspace.id}`}
              aria-current={workspace.id === currentId ? "page" : undefined}
              className="block h-8 truncate rounded-md px-2 leading-8 text-muted-foreground transition-colors duration-120 hover:bg-muted hover:text-foreground aria-[current=page]:bg-primary-subtle aria-[current=page]:font-medium aria-[current=page]:text-foreground"
            >
              {workspace.name}
            </Link>
          </li>
        ))}
      </ul>
      <Link
        href="/"
        className="mt-1 flex h-8 items-center rounded-md border-t border-border px-2 text-muted-foreground transition-colors duration-120 hover:bg-muted hover:text-foreground"
      >
        All Workspaces
      </Link>
    </>
  );
}

/**
 * The sidebar inside a Workspace (prototype "Workspace" screen): the Workspace switcher
 * and the folder tree, whose selection is the `folder` URL parameter.
 */
export function WorkspaceSidebar({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const name = workspace.data?.name ?? "";
  const { root, folder, select } = useWorkspaceFolders(workspaceId, name);

  return (
    <>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={`Workspace: ${name || "loading"}. Switch Workspace`}
            className="flex h-10 w-full items-center gap-2 rounded-md border border-border bg-surface-raised px-2 text-start font-medium text-foreground transition-colors duration-120 outline-primary hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 data-[state=open]:bg-muted"
          >
            <span
              aria-hidden
              className="grid size-6 shrink-0 place-items-center rounded-md bg-primary text-2xs font-semibold text-primary-foreground"
            >
              {initials(name)}
            </span>
            <span className="min-w-0 flex-1 truncate">{name}</span>
            <ChevronsUpDown
              aria-hidden
              className="size-4 shrink-0 text-subtle-foreground"
            />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="w-56 p-1"
          aria-label="Workspaces"
        >
          <SwitcherList currentId={workspaceId} />
        </PopoverContent>
      </Popover>
      {workspace.data && (
        <>
          <p className="px-2 pt-4 pb-1 text-2xs font-medium tracking-wider text-subtle-foreground uppercase">
            Workspace
          </p>
          <nav aria-label="Folders">
            <FolderTree root={root} selected={folder.id} onSelect={select} />
          </nav>
        </>
      )}
    </>
  );
}
