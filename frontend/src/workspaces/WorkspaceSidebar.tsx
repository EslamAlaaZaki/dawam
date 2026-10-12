"use client";

import { ChevronsUpDown } from "lucide-react";
import Link from "next/link";

import { useWorkspace, useWorkspaces } from "../api/workspaces";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { FolderTree } from "./FolderTree";
import { useWorkspaceFolders } from "./useWorkspaceFolders";

/** The Workspace's id when `pathname` is a Workspace's page (not "new", not a sub-page). */
export function workspaceIdOf(pathname: string): string | null {
  const id = /^\/workspaces\/([^/]+)$/.exec(pathname)?.[1];
  return id && id !== "new" ? decodeURIComponent(id) : null;
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
        <p className="px-2 py-1 text-sand-11">Loading…</p>
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
              className="block truncate rounded-md px-2 py-[5px] text-sand-11 hover:bg-sand-3 hover:text-sand-12 aria-[current=page]:bg-sand-4 aria-[current=page]:font-medium aria-[current=page]:text-sand-12"
            >
              {workspace.name}
            </Link>
          </li>
        ))}
      </ul>
      <Link
        href="/"
        className="mt-1 block rounded-md border-t border-sand-4 px-2 pt-2 pb-1 text-sand-11 hover:text-sand-12"
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
            className="flex w-full items-center gap-2 rounded-lg border border-line bg-transparent p-2 text-left font-semibold text-sand-12 hover:bg-sand-3"
          >
            <span
              aria-hidden
              className="grid size-[22px] shrink-0 place-items-center rounded-[5px] bg-accent-solid text-2xs font-bold text-on-accent"
            >
              {initials(name)}
            </span>
            <span className="min-w-0 flex-1 truncate">{name}</span>
            <ChevronsUpDown
              aria-hidden
              className="size-4 shrink-0 text-sand-11"
            />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="w-[220px] p-1.5"
          aria-label="Workspaces"
        >
          <SwitcherList currentId={workspaceId} />
        </PopoverContent>
      </Popover>
      {workspace.data && (
        <>
          <p className="px-2 pt-3.5 pb-1 text-2xs font-medium tracking-[0.06em] text-sand-11 uppercase">
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
