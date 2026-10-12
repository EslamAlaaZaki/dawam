"use client";

import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import { useDeleteSourceSystem } from "../api/deletions";
import {
  useCreateSourceSystem,
  useUpdateSourceSystem,
  type SourceSystem,
} from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Textarea } from "../components/ui/textarea";
import {
  Actions,
  DetailList,
  EmptyState,
  ErrorText,
  Hint,
  Panel,
  TableCard,
  formCardClasses,
  h3Classes,
  h4Classes,
  labelClasses,
  monoClasses,
} from "./parts";

const CODE_HINT =
  "Lowercase letters, digits and underscores, starting with a letter (e.g. cbs). It is used in generated names.";

interface Fields {
  name: string;
  code: string;
  description: string;
  business_owner: string;
  technical_owner: string;
}

function readFields(form: HTMLFormElement): Fields {
  const data = new FormData(form);
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
          className={`${monoClasses} read-only:bg-sand-2 read-only:text-sand-11`}
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
 * The Systems folder: every Source System, and for owners and editors a form to add one.
 * `onOpen` selects a system's folder.
 */
export function SystemsPanel({
  workspace,
  systems,
  onOpen,
}: {
  workspace: Workspace;
  systems: readonly SourceSystem[];
  onOpen: (system: SourceSystem) => void;
}) {
  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <h3 id="folder-title" className={h3Classes}>
        Systems
      </h3>
      {systems.length === 0 ? (
        <EmptyState>
          No Source Systems yet. Each Source System you add will get its own
          folder here.
        </EmptyState>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col">System Code</TableHead>
                <TableHead scope="col">Business owner</TableHead>
                <TableHead scope="col">Technical owner</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {systems.map((system) => (
                <TableRow key={system.id}>
                  <TableCell>
                    <Button
                      type="button"
                      size="sm"
                      onClick={() => onOpen(system)}
                    >
                      {system.name}
                    </Button>
                  </TableCell>
                  <TableCell className={monoClasses}>{system.code}</TableCell>
                  <TableCell>{system.business_owner || "—"}</TableCell>
                  <TableCell>{system.technical_owner || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {allows(workspace, "source_system.create") ? (
        <AddSystemForm workspaceId={workspace.id} onAdded={onOpen} />
      ) : (
        <Hint>Only owners and editors can add Source Systems.</Hint>
      )}
    </section>
  );
}

function AddSystemForm({
  workspaceId,
  onAdded,
}: {
  workspaceId: string;
  onAdded: (system: SourceSystem) => void;
}) {
  const create = useCreateSourceSystem(workspaceId);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    create.mutate(readFields(form), {
      onSuccess: (system) => {
        form.reset();
        onAdded(system);
      },
    });
  }

  return (
    <form
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-label="Add Source System"
    >
      <h4 className={h4Classes}>Add a Source System</h4>
      <DetailFields codeEditable />
      {create.isError && <ErrorText>{create.error.message}</ErrorText>}
      <Actions>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          Add Source System
        </Button>
      </Actions>
    </form>
  );
}

/**
 * One Source System's details: editable by owners and editors, read-only for viewers;
 * only owners can change the System Code.
 */
export function SystemDetails({
  workspace,
  system,
  reload,
}: {
  workspace: Workspace;
  system: SourceSystem;
  reload: () => void;
}) {
  const update = useUpdateSourceSystem(workspace.id, system.id);

  if (!allows(workspace, "source_system.edit")) {
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
        <Hint>Only owners and editors can edit a Source System.</Hint>
      </Panel>
    );
  }

  const versionConflict =
    update.error instanceof ApiError &&
    update.error.code === "version_conflict";

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // The form remounts on the new version, so a callback on mutate() would be dropped.
    update
      .mutateAsync({
        version: system.version,
        ...readFields(event.currentTarget),
      })
      .then(
        () => toast.success("Saved."),
        () => {},
      );
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form
      key={system.version}
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-labelledby="folder-title"
    >
      <h3 id="folder-title" className={h3Classes}>
        {system.name}
      </h3>
      <DetailFields
        initial={system}
        codeEditable={allows(workspace, "source_system.change_code")}
      />
      {versionConflict ? (
        <div
          className="flex flex-col items-start gap-2 text-red-11"
          role="alert"
        >
          <p>
            Someone else changed this Source System since you opened it. Reload
            it to see their changes, then edit again.
          </p>
          <Button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </Button>
        </div>
      ) : (
        update.isError && <ErrorText>{update.error.message}</ErrorText>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save
        </Button>
        {allows(workspace, "objects.delete") && (
          <DeleteSystemButton workspaceId={workspace.id} system={system} />
        )}
      </Actions>
    </form>
  );
}

/** Soft-delete (owners only): the system moves to the deleted list until purged there. */
function DeleteSystemButton({
  workspaceId,
  system,
}: {
  workspaceId: string;
  system: SourceSystem;
}) {
  const remove = useDeleteSourceSystem(workspaceId, system.id);
  return (
    <>
      <Button
        type="button"
        className="text-red-11"
        disabled={remove.isPending}
        onClick={() => remove.mutate()}
      >
        Delete Source System
      </Button>
      {remove.isError && <ErrorText>{remove.error.message}</ErrorText>}
    </>
  );
}
