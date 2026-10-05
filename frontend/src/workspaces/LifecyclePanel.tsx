"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import {
  allows,
  useArchiveWorkspace,
  useDeleteWorkspace,
  useUnarchiveWorkspace,
  type Workspace,
} from "../api/workspaces";

/**
 * Archiving, unarchiving and deleting a Workspace (stories 35, 36), for those the API
 * lets: an archived Workspace is read-only, and deleting needs its name typed.
 */
export function LifecyclePanel({ workspace }: { workspace: Workspace }) {
  const archive = useArchiveWorkspace(workspace.id);
  const unarchive = useUnarchiveWorkspace(workspace.id);
  const canArchive = allows(workspace, "workspace.archive");
  const canUnarchive = allows(workspace, "workspace.unarchive");
  const canDelete = allows(workspace, "workspace.delete");
  if (!canArchive && !canUnarchive && !canDelete) {
    return null;
  }
  const error = archive.error ?? unarchive.error;

  return (
    <section aria-labelledby="lifecycle-title">
      <h3 id="lifecycle-title">Archive and delete</h3>
      <div className="form-actions">
        {canArchive && (
          <button
            type="button"
            className="secondary"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
          >
            Archive Workspace
          </button>
        )}
        {canUnarchive && (
          <button type="button" disabled={unarchive.isPending} onClick={() => unarchive.mutate()}>
            Unarchive Workspace
          </button>
        )}
      </div>
      {canArchive && <p>Archiving makes the Workspace read-only. You can unarchive it later.</p>}
      {error && (
        <p className="form-error" role="alert">
          {error.message}
        </p>
      )}
      {canDelete && <DeleteWorkspace workspace={workspace} />}
    </section>
  );
}

function DeleteWorkspace({ workspace }: { workspace: Workspace }) {
  const remove = useDeleteWorkspace(workspace.id);
  const router = useRouter();
  const [typed, setTyped] = useState("");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    remove.mutate(typed, { onSuccess: () => router.push("/") });
  }

  return (
    <form className="form" onSubmit={onSubmit} aria-label="Delete Workspace">
      <p>
        Deleting is permanent and removes everything in the Workspace. Type its name,{" "}
        <strong>{workspace.name}</strong>, to confirm.
      </p>
      <label>
        Workspace name
        <input value={typed} onChange={(event) => setTyped(event.target.value)} />
      </label>
      {remove.isError && (
        <p className="form-error" role="alert">
          {remove.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={typed !== workspace.name || remove.isPending}>
          Delete Workspace
        </button>
      </div>
    </form>
  );
}
