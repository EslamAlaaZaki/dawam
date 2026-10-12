"use client";

import { Plus } from "lucide-react";
import Link from "next/link";

import { ROLE_LABELS, useWorkspaces } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";

/** The Workspaces the signed-in user is a member of, with their role in each. */
export function WorkspaceListPage() {
  const workspaces = useWorkspaces();
  const items = workspaces.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-[-0.01em] text-balance">Workspaces</h2>
          <p className="mt-0.5 text-sand-11">The Workspaces you are a member of.</p>
        </div>
        <Button asChild variant="primary" className="ml-auto">
          <Link href="/workspaces/new">
            <Plus aria-hidden />
            New Workspace
          </Link>
        </Button>
      </div>
      {workspaces.isPending ? (
        <Loading />
      ) : workspaces.isError ? (
        <p role="alert" className="text-red-11">
          Could not load your Workspaces: {workspaces.error.message}
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-dashed border-sand-7 p-6 text-sand-11">
          You are not a member of any Workspace yet. Create one to get started.
        </p>
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-line bg-sand-1">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Name</TableHead>
                  <TableHead scope="col">Business domain</TableHead>
                  <TableHead scope="col">Your role</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((workspace) => (
                  <TableRow key={workspace.id}>
                    <TableCell>
                      <Link
                        href={`/workspaces/${workspace.id}`}
                        className="font-medium text-jade-11 underline decoration-jade-7 underline-offset-2 hover:decoration-current"
                      >
                        {workspace.name}
                      </Link>
                    </TableCell>
                    <TableCell>{workspace.domain}</TableCell>
                    <TableCell>
                      <Badge>{ROLE_LABELS[workspace.role]}</Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {workspaces.hasNextPage && (
            <Button
              className="self-start"
              onClick={() => workspaces.fetchNextPage()}
              disabled={workspaces.isFetchingNextPage}
            >
              Show more
            </Button>
          )}
        </>
      )}
    </section>
  );
}
