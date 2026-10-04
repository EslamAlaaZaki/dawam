"use client";

import Link from "next/link";

import { ROLE_LABELS, useWorkspaces } from "../api/workspaces";
import { Loading } from "../shell/Loading";

/** The Workspaces the signed-in user is a member of, with their role in each. */
export function WorkspaceListPage() {
  const workspaces = useWorkspaces();
  const items = workspaces.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className="page">
      <div className="page-heading">
        <h2>Workspaces</h2>
        <Link className="button" href="/workspaces/new">
          New Workspace
        </Link>
      </div>
      {workspaces.isPending ? (
        <Loading />
      ) : workspaces.isError ? (
        <p role="alert">Could not load your Workspaces: {workspaces.error.message}</p>
      ) : items.length === 0 ? (
        <p>You are not a member of any Workspace yet. Create one to get started.</p>
      ) : (
        <>
          <table className="workspace-list">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Business domain</th>
                <th scope="col">Your role</th>
              </tr>
            </thead>
            <tbody>
              {items.map((workspace) => (
                <tr key={workspace.id}>
                  <td>
                    <Link href={`/workspaces/${workspace.id}`}>{workspace.name}</Link>
                  </td>
                  <td>{workspace.domain}</td>
                  <td>{ROLE_LABELS[workspace.role]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {workspaces.hasNextPage && (
            <button
              type="button"
              onClick={() => workspaces.fetchNextPage()}
              disabled={workspaces.isFetchingNextPage}
            >
              Show more
            </button>
          )}
        </>
      )}
    </section>
  );
}
