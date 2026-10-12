"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";

import { useCreateWorkspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { PageHead } from "../admin/parts";
import { DetailsFields, readDetails } from "./DetailsFields";
import { Actions, ErrorText, formCardClasses } from "./parts";

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
    <section className="flex flex-col gap-5">
      <PageHead
        title="New Workspace"
        description="You become its owner and can add members once it exists."
      />
      <form
        className={formCardClasses}
        onSubmit={onSubmit}
        aria-label="New Workspace"
      >
        <DetailsFields />
        {create.isError && <ErrorText>{create.error.message}</ErrorText>}
        <Actions>
          <Button type="submit" variant="primary" disabled={create.isPending}>
            Create Workspace
          </Button>
          <Button asChild variant="ghost">
            <Link href="/">Cancel</Link>
          </Button>
        </Actions>
      </form>
    </section>
  );
}
