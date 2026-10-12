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
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { FixImpacts } from "./FixImpacts";
import {
  Actions,
  ErrorText,
  Hint,
  Panel,
  h3Classes,
  labelClasses,
} from "./parts";

/**
 * Archiving, unarchiving and deleting a Workspace (stories 35, 36), for those the API
 * lets: an archived Workspace is read-only, and deleting needs its name typed. Once
 * unarchived, "Fix impacts" is offered (story 113a).
 */
export function LifecyclePanel({ workspace }: { workspace: Workspace }) {
  const archive = useArchiveWorkspace(workspace.id);
  const unarchive = useUnarchiveWorkspace(workspace.id);
  const canArchive = allows(workspace, "workspace.archive");
  const canUnarchive = allows(workspace, "workspace.unarchive");
  const canDelete = allows(workspace, "workspace.delete");
  const canFixImpacts =
    allows(workspace, "change_set.review") && workspace.status !== "archived";
  if (!canArchive && !canUnarchive && !canDelete) {
    return null;
  }
  const error = archive.error ?? unarchive.error;

  return (
    <Panel aria-labelledby="lifecycle-title">
      <h3 id="lifecycle-title" className={h3Classes}>
        Archive and delete
      </h3>
      <Actions>
        {canArchive && (
          <Button
            type="button"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
          >
            Archive Workspace
          </Button>
        )}
        {canUnarchive && (
          <Button
            type="button"
            variant="primary"
            disabled={unarchive.isPending}
            onClick={() => unarchive.mutate()}
          >
            Unarchive Workspace
          </Button>
        )}
      </Actions>
      {canArchive && (
        <Hint>
          Archiving makes the Workspace read-only. You can unarchive it later.
        </Hint>
      )}
      {unarchive.isSuccess && canFixImpacts && (
        <>
          <p>
            Archiving rejected the pending Change Sets. Fix impacts to propose
            again what propagation can fix.
          </p>
          <FixImpacts workspace={workspace} />
        </>
      )}
      {error && <ErrorText>{error.message}</ErrorText>}
      {canDelete && <DeleteWorkspace workspace={workspace} />}
    </Panel>
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
    <form
      className="flex max-w-xl flex-col gap-3.5 border-t border-sand-4 pt-4"
      onSubmit={onSubmit}
      aria-label="Delete Workspace"
    >
      <p>
        Deleting is permanent and removes everything in the Workspace. Type its
        name, <strong>{workspace.name}</strong>, to confirm.
      </p>
      <label className={labelClasses}>
        Workspace name
        <Input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
        />
      </label>
      {remove.isError && <ErrorText>{remove.error.message}</ErrorText>}
      <Actions>
        <Button
          type="submit"
          className="text-red-11"
          disabled={typed !== workspace.name || remove.isPending}
        >
          Delete Workspace
        </Button>
      </Actions>
    </form>
  );
}
