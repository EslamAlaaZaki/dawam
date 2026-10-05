"use client";

import { useState, type FormEvent } from "react";

import {
  useAdminWorkspaces,
  useReassignOwner,
  type AdminWorkspace,
} from "../api/adminWorkspaces";
import { useUsers } from "../api/users";
import { Loading } from "../shell/Loading";

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
    <section className="page admin-page">
      <h2>Workspaces</h2>
      {workspaces.isPending ? (
        <Loading />
      ) : workspaces.isError ? (
        <p role="alert">Could not load the Workspaces: {workspaces.error.message}</p>
      ) : (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Status</th>
                <th scope="col">Owners</th>
                <th scope="col">Members</th>
                <th scope="col">Created</th>
                <th scope="col">Updated</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {workspaces.data.pages.flatMap((page) =>
                page.items.map((workspace) => (
                  <WorkspaceRow key={workspace.id} workspace={workspace} />
                )),
              )}
            </tbody>
          </table>
          {workspaces.data.pages[0]?.items.length === 0 && <p>There are no Workspaces.</p>}
          {workspaces.hasNextPage && (
            <button
              type="button"
              onClick={() => void workspaces.fetchNextPage()}
              disabled={workspaces.isFetchingNextPage}
            >
              Load more
            </button>
          )}
        </>
      )}
    </section>
  );
}

function WorkspaceRow({ workspace }: { workspace: AdminWorkspace }) {
  return (
    <tr aria-label={workspace.name}>
      <td>{workspace.name}</td>
      <td>{workspace.status === "archived" ? "Archived" : "Active"}</td>
      <td>
        {workspace.owners.map((owner) => (
          <div key={owner.user_id}>
            {owner.display_name} ({owner.email})
            {!owner.is_active && " — deactivated"}
          </div>
        ))}
      </td>
      <td>{workspace.member_count}</td>
      <td>{day(workspace.created_at)}</td>
      <td>{day(workspace.updated_at)}</td>
      <td>{!workspace.has_active_owner && <Reassign workspace={workspace} />}</td>
    </tr>
  );
}

/** Picks an active user to become the owner of a Workspace nobody active owns. */
function Reassign({ workspace }: { workspace: AdminWorkspace }) {
  const [asking, setAsking] = useState(false);
  const reassign = useReassignOwner();
  const users = useUsers({ active: true });
  const candidates = users.data?.pages.flatMap((page) => page.items) ?? [];

  if (!asking) {
    return (
      <button type="button" className="secondary" onClick={() => setAsking(true)}>
        Reassign ownership
      </button>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const userId = String(new FormData(event.currentTarget).get("user") ?? "");
    if (userId) {
      reassign.mutate({ workspaceId: workspace.id, userId });
    }
  }

  return (
    <form className="admin-actions" onSubmit={onSubmit} aria-label={`Reassign ${workspace.name}`}>
      <label>
        New owner
        <select name="user" defaultValue="">
          <option value="" disabled>
            Choose a user
          </option>
          {candidates.map((user) => (
            <option key={user.id} value={user.id}>
              {user.display_name} ({user.email})
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={reassign.isPending}>
        Make owner
      </button>
      <button type="button" className="secondary" onClick={() => setAsking(false)}>
        Cancel
      </button>
      {reassign.isError && (
        <p className="form-error" role="alert">
          {reassign.error.message}
        </p>
      )}
    </form>
  );
}
