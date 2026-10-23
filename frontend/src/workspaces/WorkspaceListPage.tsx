"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  useNewWorkspaceModels,
  type ModelChoice,
  type NewWorkspaceModels,
} from "../api/workspaceAi";
import {
  ROLE_LABELS,
  allows,
  useArchiveWorkspace,
  useCreateWorkspace,
  useDeleteWorkspace,
  useUnarchiveWorkspace,
  useUpdateWorkspace,
  useWorkspaces,
  type Workspace,
} from "../api/workspaces";
import { PageHeader } from "../components/PageHeader";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import {
  EntityTable,
  type EntityColumn,
} from "../components/entity/EntityTable";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { DetailsFields, detailsFrom } from "./DetailsFields";
import { ErrorText, Hint, NativeSelect, labelClasses } from "./parts";

const COLUMNS: EntityColumn<Workspace>[] = [
  {
    header: "Name",
    cell: (workspace) => (
      <Link
        href={`/workspaces/${workspace.id}`}
        className="font-medium text-primary underline underline-offset-2 hover:text-primary-hover"
      >
        {workspace.name}
      </Link>
    ),
  },
  {
    header: "Business domain",
    cell: (workspace) => (
      <span className="text-muted-foreground">{workspace.domain}</span>
    ),
  },
  {
    header: "Your role",
    cell: (workspace) => <Badge variant="accent">{ROLE_LABELS[workspace.role]}</Badge>,
  },
];

/** The Workspaces the signed-in user is a member of, with their role in each. */
export function WorkspaceListPage() {
  const workspaces = useWorkspaces();
  const [creating, setCreating] = useState(false);
  const createButton = (
    <Button variant="primary" onClick={() => setCreating(true)}>
      <Plus aria-hidden />
      New workspace
    </Button>
  );

  return (
    <section className="flex flex-col gap-5">
      <PageHeader
        title="Workspaces"
        description="The Workspaces you are a member of."
        actions={createButton}
      />
      <EntityTable
        rows={workspaces.data?.pages.flatMap((page) => page.items)}
        rowKey={(workspace) => workspace.id}
        rowLabel={(workspace) => workspace.name}
        columns={COLUMNS}
        actions={(workspace) => <WorkspaceActions workspace={workspace} />}
        isPending={workspaces.isPending}
        error={workspaces.error}
        loadErrorPrefix="Could not load your Workspaces:"
        emptyMessage="You are not a member of any Workspace yet. Create one to get started."
        emptyAction={createButton}
        hasMore={workspaces.hasNextPage}
        isLoadingMore={workspaces.isFetchingNextPage}
        onLoadMore={() => void workspaces.fetchNextPage()}
      />
      <CreateWorkspaceDialog open={creating} onOpenChange={setCreating} />
    </section>
  );
}

/** Create a Workspace; the creator becomes its owner and lands on it. */
function CreateWorkspaceDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateWorkspace();
  const router = useRouter();
  const models = useNewWorkspaceModels();
  const picked = (form: FormData, field: string) =>
    String(form.get(field) ?? "") || null;

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) create.reset();
        onOpenChange(next);
      }}
      title="New workspace"
      description="You become its owner and can add members once it exists."
      submitLabel="Create Workspace"
      onSubmit={(form) =>
        create.mutate(
          {
            ...detailsFrom(form),
            agent_model_id: picked(form, "agent"),
            light_model_id: picked(form, "light"),
            embedding_model_id: picked(form, "embedding"),
          },
          {
            onSuccess: (workspace) =>
              router.push(`/workspaces/${workspace.id}`),
          },
        )
      }
      pending={create.isPending}
      error={create.error}
    >
      <DetailsFields />
      {models.data && <NewWorkspaceModelFields offered={models.data} />}
    </EntityFormDialog>
  );
}

/**
 * The new Workspace's models: its default agent model (required when there is one to
 * choose), an optional light model and its embedding model (optional only when no model
 * has the Embedding role). An internal-only Workspace is offered internal models only.
 */
function NewWorkspaceModelFields({ offered }: { offered: NewWorkspaceModels }) {
  const { agent, light, embedding } = offered.choices;
  const option = (m: ModelChoice) => (
    <option key={m.id} value={m.id}>
      {m.name} ({m.provider_name})
    </option>
  );
  return (
    <>
      {offered.internal_only && (
        <Hint>
          This Workspace starts internal-only, so only internal models are
          offered.
        </Hint>
      )}
      {agent.length > 0 && (
        <label className={labelClasses}>
          Default agent model
          <NativeSelect name="agent" required defaultValue={agent[0]?.id}>
            {agent.map(option)}
          </NativeSelect>
        </label>
      )}
      {light.length > 0 && (
        <label className={labelClasses}>
          Light model
          <NativeSelect name="light" defaultValue="">
            <option value="">None: light tasks use the agent model</option>
            {light.map(option)}
          </NativeSelect>
        </label>
      )}
      {embedding.length > 0 ? (
        <>
          <label className={labelClasses}>
            Embedding model
            <NativeSelect
              name="embedding"
              required
              defaultValue={embedding[0]?.id}
            >
              {embedding.map(option)}
            </NativeSelect>
          </label>
          <Hint>
            Used to search this workspace&apos;s documents. It can be changed
            until documents are indexed.
          </Hint>
        </>
      ) : (
        <Hint>
          No model has the Embedding role yet, so document search is unavailable
          until an owner sets an embedding model.
        </Hint>
      )}
    </>
  );
}

/** What the member's role lets them do to one Workspace. */
function WorkspaceActions({ workspace }: { workspace: Workspace }) {
  const archive = useArchiveWorkspace(workspace.id);
  const unarchive = useUnarchiveWorkspace(workspace.id);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const problem = archive.error ?? unarchive.error;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {allows(workspace, "workspace.edit") && (
          <Button size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
        {allows(workspace, "workspace.archive") && (
          <Button
            size="sm"
            disabled={archive.isPending}
            onClick={() => archive.mutate()}
          >
            Archive
          </Button>
        )}
        {allows(workspace, "workspace.unarchive") && (
          <Button
            size="sm"
            disabled={unarchive.isPending}
            onClick={() => unarchive.mutate()}
          >
            Unarchive
          </Button>
        )}
        {allows(workspace, "workspace.delete") && (
          <Button
            size="sm"
            className="text-danger-strong"
            onClick={() => setDeleting(true)}
          >
            Delete
          </Button>
        )}
      </div>
      {problem && <ErrorText>{problem.message}</ErrorText>}
      <EditWorkspaceDialog
        workspace={workspace}
        open={editing}
        onOpenChange={setEditing}
      />
      <DeleteWorkspaceDialog
        workspace={workspace}
        open={deleting}
        onOpenChange={setDeleting}
      />
    </>
  );
}

function EditWorkspaceDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: Workspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateWorkspace(workspace.id);

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) update.reset();
        onOpenChange(next);
      }}
      title="Edit workspace"
      description={workspace.name}
      submitLabel="Save"
      onSubmit={(form) =>
        update.mutate(
          { version: workspace.version, ...detailsFrom(form) },
          {
            onSuccess: () => {
              toast.success("Saved.");
              onOpenChange(false);
            },
          },
        )
      }
      pending={update.isPending}
      error={update.error}
    >
      <DetailsFields initial={workspace} />
    </EntityFormDialog>
  );
}

/** Deleting is permanent, so the Workspace's name is typed to confirm. */
function DeleteWorkspaceDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: Workspace;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const remove = useDeleteWorkspace(workspace.id);
  const [typed, setTyped] = useState("");

  return (
    <EntityFormDialog
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
          Deleting is permanent and removes everything in the Workspace. Type
          its name, <strong>{workspace.name}</strong>, to confirm.
        </>
      }
      submitLabel="Delete Workspace"
      onSubmit={() => remove.mutate(workspace.name)}
      pending={remove.isPending || typed !== workspace.name}
      error={remove.error}
    >
      <label className={labelClasses}>
        Workspace name
        <Input
          value={typed}
          autoComplete="off"
          onChange={(event) => setTyped(event.target.value)}
        />
      </label>
    </EntityFormDialog>
  );
}
