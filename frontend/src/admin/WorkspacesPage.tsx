"use client";

import { useState } from "react";

import {
  useAdminWorkspaces,
  useReassignOwner,
  type AdminWorkspace,
} from "../api/adminWorkspaces";
import { useUsers } from "../api/users";
import { useArchiveWorkspace, useDeleteWorkspace } from "../api/workspaces";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { NativeSelect, PageHead, labelClasses } from "./parts";

function day(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

const WORKSPACE_COLUMNS: EntityColumn<AdminWorkspace>[] = [
  { header: "Name", className: "font-medium", cell: (workspace) => workspace.name },
  {
    header: "Status",
    cell: (workspace) => (
      <Badge variant={workspace.status === "archived" ? "neutral" : "success"} dot>
        {workspace.status === "archived" ? "Archived" : "Active"}
      </Badge>
    ),
  },
  {
    header: "Owners",
    cell: (workspace) =>
      workspace.owners.map((owner) => (
        <div key={owner.user_id}>
          {owner.display_name} ({owner.email})
          {!owner.is_active && " — deactivated"}
        </div>
      )),
  },
  { header: "Members", cell: (workspace) => workspace.member_count },
  { header: "Created", className: "text-sand-11", cell: (workspace) => day(workspace.created_at) },
  { header: "Updated", className: "text-sand-11", cell: (workspace) => day(workspace.updated_at) },
];

/**
 * Every Workspace's metadata (story 21): name, owners, member count and dates, never
 * its content. A Workspace whose owners are all deactivated can be given a new owner
 * (story 22); an active one can be archived, and an archived one deleted.
 */
export function WorkspacesPage() {
  const workspaces = useAdminWorkspaces();

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Workspaces"
        description="Every Workspace's name, owners and size. Never its content."
      />
      <EntityTable
        rows={workspaces.data?.pages.flatMap((page) => page.items)}
        rowKey={(workspace) => workspace.id}
        rowLabel={(workspace) => workspace.name}
        columns={WORKSPACE_COLUMNS}
        actions={(workspace) => <WorkspaceActions workspace={workspace} />}
        isPending={workspaces.isPending}
        error={workspaces.error}
        loadErrorPrefix="Could not load the Workspaces:"
        emptyMessage="There are no Workspaces."
        count="Workspaces"
        hasMore={workspaces.hasNextPage}
        isLoadingMore={workspaces.isFetchingNextPage}
        onLoadMore={() => void workspaces.fetchNextPage()}
      />
    </section>
  );
}

/** What an admin can do to one Workspace: reassign when orphaned, archive, delete once archived. */
function WorkspaceActions({ workspace }: { workspace: AdminWorkspace }) {
  const [reassigning, setReassigning] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const archived = workspace.status === "archived";

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {!workspace.has_active_owner && (
          <Button size="sm" onClick={() => setReassigning(true)}>
            Reassign ownership
          </Button>
        )}
        {!archived && (
          <Button size="sm" onClick={() => setArchiving(true)}>
            Archive
          </Button>
        )}
        {archived && (
          <Button size="sm" className="text-red-11" onClick={() => setDeleting(true)}>
            Delete
          </Button>
        )}
      </div>
      {!workspace.has_active_owner && (
        <ReassignDialog workspace={workspace} open={reassigning} onOpenChange={setReassigning} />
      )}
      {!archived && (
        <ArchiveDialog workspace={workspace} open={archiving} onOpenChange={setArchiving} />
      )}
      {archived && (
        <DeleteDialog workspace={workspace} open={deleting} onOpenChange={setDeleting} />
      )}
    </>
  );
}

function ArchiveDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: AdminWorkspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const archive = useArchiveWorkspace(workspace.id);
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) archive.reset();
        onOpenChange(next);
      }}
      title={`Archive ${workspace.name}?`}
      description="Archiving makes the Workspace read-only. Its owners can unarchive it later."
      confirmLabel="Archive"
      pending={archive.isPending}
      error={archive.error}
      onConfirm={() =>
        archive.mutate(undefined, {
          onSuccess: () => {
            toast.success(`${workspace.name} archived.`);
            onOpenChange(false);
          },
        })
      }
    />
  );
}

/** Deleting is permanent: the Workspace's name must be typed to confirm. */
function DeleteDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: AdminWorkspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const remove = useDeleteWorkspace(workspace.id);
  const [typed, setTyped] = useState("");
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          remove.reset();
          setTyped("");
        }
        onOpenChange(next);
      }}
      title={`Delete ${workspace.name}?`}
      description={
        <>
          Deleting is permanent and removes everything in the Workspace. Type its name,{" "}
          <strong>{workspace.name}</strong>, to confirm.
        </>
      }
      confirmLabel="Delete Workspace"
      confirmDisabled={typed !== workspace.name}
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() =>
        remove.mutate(typed, {
          onSuccess: () => {
            toast.success(`${workspace.name} deleted.`);
            onOpenChange(false);
          },
        })
      }
    >
      <label className={labelClasses}>
        Workspace name
        <Input value={typed} onChange={(event) => setTyped(event.target.value)} />
      </label>
    </ConfirmDialog>
  );
}

/** Picks an active user to become the owner of a Workspace nobody active owns. */
function ReassignDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: AdminWorkspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const reassign = useReassignOwner();
  const users = useUsers({ active: true });
  const candidates = users.data?.pages.flatMap((page) => page.items) ?? [];

  function onSubmit(form: FormData) {
    const userId = String(form.get("user") ?? "");
    if (userId) {
      reassign.mutate(
        { workspaceId: workspace.id, userId },
        {
          onSuccess: () => {
            onOpenChange(false);
            toast.success(`New owner set for ${workspace.name}.`);
          },
        },
      );
    }
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reassign.reset();
        onOpenChange(next);
      }}
      title={`Reassign ${workspace.name}`}
      description="Every owner is deactivated: choose an active user to become the owner."
      submitLabel="Make owner"
      onSubmit={onSubmit}
      pending={reassign.isPending}
      error={reassign.error}
    >
      <label className={labelClasses}>
        New owner
        <NativeSelect name="user" defaultValue="">
          <option value="" disabled>
            Choose a user
          </option>
          {candidates.map((user) => (
            <option key={user.id} value={user.id}>
              {user.display_name} ({user.email})
            </option>
          ))}
        </NativeSelect>
      </label>
    </EntityFormDialog>
  );
}
