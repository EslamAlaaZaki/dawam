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
import { PageHead } from "../admin/parts";
import { Loading } from "../shell/Loading";
import { EmptyState, ErrorText } from "./parts";

/** The Workspaces the signed-in user is a member of, with their role in each. */
export function WorkspaceListPage() {
  const workspaces = useWorkspaces();
  const items = workspaces.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Workspaces"
        description="The Workspaces you are a member of."
        actions={
          <Button asChild variant="primary">
            <Link href="/workspaces/new">
              <Plus aria-hidden />
              New Workspace
            </Link>
          </Button>
        }
      />
      {workspaces.isPending ? (
        <Loading />
      ) : workspaces.isError ? (
        <ErrorText>
          Could not load your Workspaces: {workspaces.error.message}
        </ErrorText>
      ) : items.length === 0 ? (
        <EmptyState>
          You are not a member of any Workspace yet. Create one to get started.
        </EmptyState>
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
