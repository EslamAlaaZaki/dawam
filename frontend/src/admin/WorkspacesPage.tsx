"use client";

import { useState, type FormEvent } from "react";

import {
  useAdminWorkspaces,
  useReassignOwner,
  type AdminWorkspace,
} from "../api/adminWorkspaces";
import { useUsers } from "../api/users";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
} from "./parts";

function day(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

/**
 * Every Workspace's metadata (story 21): name, owners, member count and dates, never
 * its content. A Workspace whose owners are all deactivated can be given a new owner
 * (story 22).
 */
export function WorkspacesPage() {
  const workspaces = useAdminWorkspaces();

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Workspaces"
        description="Every Workspace's name, owners and size. Never its content."
      />
      {workspaces.isPending ? (
        <Loading />
      ) : workspaces.isError ? (
        <ErrorText>
          Could not load the Workspaces: {workspaces.error.message}
        </ErrorText>
      ) : (
        <>
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Name</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead scope="col">Owners</TableHead>
                  <TableHead scope="col">Members</TableHead>
                  <TableHead scope="col">Created</TableHead>
                  <TableHead scope="col">Updated</TableHead>
                  <TableHead scope="col">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {workspaces.data.pages.flatMap((page) =>
                  page.items.map((workspace) => (
                    <WorkspaceRow key={workspace.id} workspace={workspace} />
                  )),
                )}
              </TableBody>
            </Table>
          </TableCard>
          {workspaces.data.pages[0]?.items.length === 0 && (
            <p className="text-sand-11">There are no Workspaces.</p>
          )}
          {workspaces.hasNextPage && (
            <Button
              className="self-start"
              onClick={() => void workspaces.fetchNextPage()}
              disabled={workspaces.isFetchingNextPage}
            >
              Load more
            </Button>
          )}
        </>
      )}
    </section>
  );
}

function WorkspaceRow({ workspace }: { workspace: AdminWorkspace }) {
  return (
    <TableRow aria-label={workspace.name}>
      <TableCell className="font-medium">{workspace.name}</TableCell>
      <TableCell>
        <Badge
          variant={workspace.status === "archived" ? "neutral" : "success"}
          dot
        >
          {workspace.status === "archived" ? "Archived" : "Active"}
        </Badge>
      </TableCell>
      <TableCell>
        {workspace.owners.map((owner) => (
          <div key={owner.user_id}>
            {owner.display_name} ({owner.email})
            {!owner.is_active && " — deactivated"}
          </div>
        ))}
      </TableCell>
      <TableCell>{workspace.member_count}</TableCell>
      <TableCell className="text-sand-11">
        {day(workspace.created_at)}
      </TableCell>
      <TableCell className="text-sand-11">
        {day(workspace.updated_at)}
      </TableCell>
      <TableCell>
        {!workspace.has_active_owner && <Reassign workspace={workspace} />}
      </TableCell>
    </TableRow>
  );
}

/** Picks an active user to become the owner of a Workspace nobody active owns. */
function Reassign({ workspace }: { workspace: AdminWorkspace }) {
  const [asking, setAsking] = useState(false);
  const reassign = useReassignOwner();
  const users = useUsers({ active: true });
  const candidates = users.data?.pages.flatMap((page) => page.items) ?? [];

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const userId = String(new FormData(event.currentTarget).get("user") ?? "");
    if (userId) {
      reassign.mutate(
        { workspaceId: workspace.id, userId },
        {
          onSuccess: () => {
            setAsking(false);
            toast.success(`New owner set for ${workspace.name}.`);
          },
        },
      );
    }
  }

  return (
    <Dialog open={asking} onOpenChange={setAsking}>
      <DialogTrigger asChild>
        <Button size="sm">Reassign ownership</Button>
      </DialogTrigger>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Reassign {workspace.name}</DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-3.5"
          onSubmit={onSubmit}
          aria-label={`Reassign ${workspace.name}`}
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
          {reassign.isError && <ErrorText>{reassign.error.message}</ErrorText>}
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setAsking(false)}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={reassign.isPending}
            >
              Make owner
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
