"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";

import { useCreateWorkspace } from "../api/workspaces";
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
    <section className="page">
      <form className="form" onSubmit={onSubmit} aria-labelledby="new-workspace-title">
        <h2 id="new-workspace-title">New Workspace</h2>
        <DetailsFields />
        {create.isError && (
          <p className="form-error" role="alert">
            {create.error.message}
          </p>
        )}
        <div className="form-actions">
          <button type="submit" disabled={create.isPending}>
            Create Workspace
          </button>
          <Link href="/">Cancel</Link>
        </div>
      </form>
    </section>
  );
}
