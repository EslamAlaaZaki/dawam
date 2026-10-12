"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";

import { useCreateWorkspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { DetailsFields, readDetails } from "./DetailsFields";

/** Create a Workspace; the creator becomes its owner and lands on it. */
export function NewWorkspacePage() {
  const create = useCreateWorkspace();
  const router = useRouter();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate(readDetails(event.currentTarget), {
      onSuccess: (workspace) => router.push(`/workspaces/${workspace.id}`),
    });
  }

  return (
    <section>
      <form
        className="flex max-w-xl flex-col gap-4 rounded-xl border border-sand-5 bg-sand-1 p-6"
        onSubmit={onSubmit}
        aria-labelledby="new-workspace-title"
      >
        <h2 id="new-workspace-title" className="text-xl font-semibold">
          New Workspace
        </h2>
        <DetailsFields />
        {create.isError && (
          <p className="text-sm text-red-11" role="alert">
            {create.error.message}
          </p>
        )}
        <div className="flex items-center gap-3">
          <Button type="submit" variant="primary" disabled={create.isPending}>
            Create Workspace
          </Button>
          <Button asChild variant="ghost">
            <Link href="/">Cancel</Link>
          </Button>
        </div>
      </form>
    </section>
  );
}
