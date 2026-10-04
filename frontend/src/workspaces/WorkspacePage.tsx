"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  ROLE_LABELS,
  allows,
  useUpdateWorkspace,
  useWorkspace,
  type Workspace,
} from "../api/workspaces";
import { Loading } from "../shell/Loading";
import { DetailsFields, readDetails } from "./DetailsFields";
import { FolderTree } from "./FolderTree";
import { findFolder, workspaceFolders } from "./folders";
import { MembersPanel } from "./MembersPanel";
import { StageProgressPanel } from "./StageProgressPanel";
import { WorkspaceNotFound } from "./WorkspaceNotFound";

/**
 * A Workspace's page: its folder tree, the selected folder (kept in the `folder` URL
 * parameter, so it can be linked and reloaded) and the stage progress. The Workspace's
 * own folder shows its details, editable by owners and read-only for everyone else, and
 * its members.
 */
export function WorkspacePage({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  if (workspace.isPending) {
    return <Loading />;
  }
  if (workspace.isError) {
    const error = workspace.error;
    // 404: missing, or not the user's to see. 422: not a Workspace id at all.
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) {
      return <WorkspaceNotFound />;
    }
    return (
      <p className="page" role="alert">
        Could not load the Workspace: {error.message}
      </p>
    );
  }
  const root = workspaceFolders(workspace.data.name);
  const folder = findFolder(root, searchParams.get("folder") ?? "") ?? root;

  function select(id: string) {
    router.push(id === "" ? pathname : `${pathname}?${new URLSearchParams({ folder: id })}`);
  }

  return (
    <section className="page">
      <p>
        <Link href="/">All Workspaces</Link>
      </p>
      <h2>{workspace.data.name}</h2>
      <p>Your role: {ROLE_LABELS[workspace.data.role]}</p>
      <div className="workspace-layout">
        <nav aria-label="Folders">
          <FolderTree root={root} selected={folder.id} onSelect={select} />
        </nav>
        <div className="workspace-main">
          {folder.id === "" ? (
            <>
              <WorkspaceDetails workspace={workspace.data} reload={() => workspace.refetch()} />
              <MembersPanel workspace={workspace.data} />
            </>
          ) : (
            <section aria-labelledby="folder-title">
              <h3 id="folder-title">{folder.label}</h3>
              <p className="empty-state">{folder.empty}</p>
            </section>
          )}
          <StageProgressPanel workspaceId={workspaceId} />
        </div>
      </div>
    </section>
  );
}

function WorkspaceDetails({ workspace, reload }: { workspace: Workspace; reload: () => void }) {
  const update = useUpdateWorkspace(workspace.id);

  if (!allows(workspace, "workspace.edit")) {
    return (
      <section aria-labelledby="details-title">
        <h3 id="details-title">Details</h3>
        <dl className="details">
          <dt>Description</dt>
          <dd>{workspace.description || "—"}</dd>
          <dt>Business domain</dt>
          <dd>{workspace.domain || "—"}</dd>
        </dl>
        <p>Only owners can edit these details.</p>
      </section>
    );
  }

  const conflict = update.error instanceof ApiError && update.error.status === 409;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate({ version: workspace.version, ...readDetails(event.currentTarget) });
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form key={workspace.version} className="form" onSubmit={onSubmit} aria-labelledby="details-title">
      <h3 id="details-title">Details</h3>
      <DetailsFields initial={workspace} />
      {conflict ? (
        <div className="form-error" role="alert">
          <p>
            Someone else changed this Workspace since you opened it. Reload it to see their
            changes, then edit again.
          </p>
          <button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </button>
        </div>
      ) : (
        update.isError && (
          <p className="form-error" role="alert">
            {update.error.message}
          </p>
        )
      )}
      {update.isSuccess && <p role="status">Saved.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
      </div>
    </form>
  );
}
