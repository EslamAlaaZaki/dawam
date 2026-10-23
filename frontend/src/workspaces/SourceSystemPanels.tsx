"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/client";
import { useDeleteSourceSystem } from "../api/deletions";
import {
  useCreateSourceSystem,
  useUpdateSourceSystem,
  type SourceSystem,
} from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Textarea } from "../components/ui/textarea";
import {
  DetailList,
  Hint,
  Panel,
  h3Classes,
  labelClasses,
  monoClasses,
} from "./parts";

const CODE_HINT =
  "Lowercase letters, digits and underscores, starting with a letter (e.g. cbs). It is used in generated names.";

const CONFLICT_MESSAGE =
  "Someone else changed this Source System since you opened it. The latest version is loaded: check your edits, then save again.";

interface Fields {
  name: string;
  code: string;
  description: string;
  business_owner: string;
  technical_owner: string;
}

function readFields(data: FormData): Fields {
  const get = (name: string) => String(data.get(name) ?? "");
  return {
    name: get("name"),
    code: get("code").trim(),
    description: get("description"),
    business_owner: get("business_owner"),
    technical_owner: get("technical_owner"),
  };
}

function DetailFields({
  initial,
  codeEditable,
}: {
  initial?: SourceSystem;
  codeEditable: boolean;
}) {
  return (
    <>
      <label className={labelClasses}>
        Name
        <Input
          name="name"
          required
          maxLength={200}
          defaultValue={initial?.name}
        />
      </label>
      <label className={labelClasses}>
        System Code
        <Input
          name="code"
          required
          maxLength={24}
          autoComplete="off"
          defaultValue={initial?.code}
          readOnly={!codeEditable}
          aria-describedby="system-code-hint"
          className={`${monoClasses} read-only:bg-muted read-only:text-muted-foreground`}
        />
      </label>
      <Hint id="system-code-hint">
        {codeEditable ? CODE_HINT : "Only an owner can change the System Code."}
      </Hint>
      <label className={labelClasses}>
        Description
        <Textarea
          name="description"
          rows={3}
          maxLength={4000}
          defaultValue={initial?.description}
        />
      </label>
      <label className={labelClasses}>
        Business owner
        <Input
          name="business_owner"
          maxLength={200}
          defaultValue={initial?.business_owner}
        />
      </label>
      <label className={labelClasses}>
        Technical owner
        <Input
          name="technical_owner"
          maxLength={200}
          defaultValue={initial?.technical_owner}
        />
      </label>
    </>
  );
}

/**
 * The Systems folder: every Source System, and for owners and editors a popup to add
 * one. `onOpen` selects a system's folder; `reload` refetches the systems.
 */
export function SystemsPanel({
  workspace,
  systems,
  onOpen,
  reload,
}: {
  workspace: Workspace;
  systems: readonly SourceSystem[];
  onOpen: (system: SourceSystem) => void;
  reload: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const canCreate = allows(workspace, "source_system.create");
  const canAct =
    allows(workspace, "source_system.edit") || allows(workspace, "objects.delete");
  const addButton = canCreate ? (
    <Button variant="primary" onClick={() => setAdding(true)}>
      <Plus aria-hidden />
      Add system
    </Button>
  ) : null;
  const columns: EntityColumn<SourceSystem>[] = [
    {
      header: "Name",
      cell: (system) => (
        <Button type="button" size="sm" onClick={() => onOpen(system)}>
          {system.name}
        </Button>
      ),
    },
    {
      header: "System Code",
      className: monoClasses,
      cell: (system) => system.code,
    },
    { header: "Business owner", cell: (system) => system.business_owner || "—" },
    { header: "Technical owner", cell: (system) => system.technical_owner || "—" },
  ];

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="folder-title" className={h3Classes}>
          Systems
        </h3>
        {addButton && <span className="ml-auto">{addButton}</span>}
      </div>
      <EntityTable
        rows={[...systems]}
        rowKey={(system) => system.id}
        rowLabel={(system) => system.name}
        columns={columns}
        actions={
          canAct
            ? (system) => (
                <SystemActions workspace={workspace} system={system} reload={reload} />
              )
            : undefined
        }
        emptyMessage="No Source Systems yet. Each Source System you add will get its own folder here."
      />
      {!canCreate && <Hint>Only owners and editors can add Source Systems.</Hint>}
      {canCreate && (
        <AddSystemDialog
          workspaceId={workspace.id}
          open={adding}
          onOpenChange={setAdding}
          onAdded={onOpen}
        />
      )}
    </section>
  );
}

function AddSystemDialog({
  workspaceId,
  open,
  onOpenChange,
  onAdded,
}: {
  workspaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded: (system: SourceSystem) => void;
}) {
  const create = useCreateSourceSystem(workspaceId);

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) create.reset();
        onOpenChange(next);
      }}
      title="Add Source System"
      description="Each Source System gets its own folder in the Workspace."
      submitLabel="Add Source System"
      onSubmit={(form) =>
        create.mutate(readFields(form), {
          onSuccess: (system) => {
            onOpenChange(false);
            onAdded(system);
          },
        })
      }
      pending={create.isPending}
      error={create.error}
    >
      <DetailFields codeEditable />
    </EntityFormDialog>
  );
}

/**
 * What the member's role lets them do to one Source System: edit it (owners and editors,
 * but only owners change the System Code) and delete it (owners; a soft delete).
 */
function SystemActions({
  workspace,
  system,
  reload,
}: {
  workspace: Workspace;
  system: SourceSystem;
  reload: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const remove = useDeleteSourceSystem(workspace.id, system.id);

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {allows(workspace, "source_system.edit") && (
          <Button size="sm" onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
        {allows(workspace, "objects.delete") && (
          <Button
            size="sm"
            className="text-danger-strong"
            onClick={() => setDeleting(true)}
          >
            Delete
          </Button>
        )}
      </div>
      <EditSystemDialog
        workspace={workspace}
        system={system}
        reload={reload}
        open={editing}
        onOpenChange={setEditing}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setDeleting(next);
        }}
        title={`Delete ${system.name}?`}
        description="It moves to the deleted list, where an owner can purge it for good."
        confirmLabel="Delete Source System"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() =>
          remove.mutate(undefined, { onSuccess: () => setDeleting(false) })
        }
      />
    </>
  );
}

function EditSystemDialog({
  workspace,
  system,
  reload,
  open,
  onOpenChange,
}: {
  workspace: Workspace;
  system: SourceSystem;
  reload: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateSourceSystem(workspace.id, system.id);
  const conflict =
    update.error instanceof ApiError && update.error.code === "version_conflict";

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) update.reset();
        onOpenChange(next);
      }}
      title="Edit Source System"
      description={system.name}
      submitLabel="Save"
      onSubmit={(form) =>
        update.mutate(
          { version: system.version, ...readFields(form) },
          {
            onSuccess: () => {
              toast.success("Saved.");
              onOpenChange(false);
            },
            // The newer version loads, so saving again works.
            onError: (error) => {
              if (error instanceof ApiError && error.code === "version_conflict") {
                reload();
              }
            },
          },
        )
      }
      pending={update.isPending}
      error={conflict ? new Error(CONFLICT_MESSAGE) : update.error}
    >
      <DetailFields
        initial={system}
        codeEditable={allows(workspace, "source_system.change_code")}
      />
    </EntityFormDialog>
  );
}

/** One Source System's details, with what the member's role lets them do to it. */
export function SystemDetails({
  workspace,
  system,
  reload,
}: {
  workspace: Workspace;
  system: SourceSystem;
  reload: () => void;
}) {
  const canAct =
    allows(workspace, "source_system.edit") || allows(workspace, "objects.delete");

  return (
    <Panel aria-labelledby="folder-title">
      <h3 id="folder-title" className={h3Classes}>
        {system.name}
      </h3>
      <DetailList>
        <dt>System Code</dt>
        <dd className={monoClasses}>{system.code}</dd>
        <dt>Description</dt>
        <dd>{system.description || "—"}</dd>
        <dt>Business owner</dt>
        <dd>{system.business_owner || "—"}</dd>
        <dt>Technical owner</dt>
        <dd>{system.technical_owner || "—"}</dd>
      </DetailList>
      {canAct ? (
        <SystemActions workspace={workspace} system={system} reload={reload} />
      ) : (
        <Hint>Only owners and editors can edit a Source System.</Hint>
      )}
    </Panel>
  );
}
