"use client";

import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  useCreateSourceSystem,
  useUpdateSourceSystem,
  type SourceSystem,
} from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";

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
      <label>
        Name
        <input name="name" required maxLength={200} defaultValue={initial?.name} />
      </label>
      <label>
        System Code
        <input
          name="code"
          required
          maxLength={24}
          autoComplete="off"
          defaultValue={initial?.code}
          readOnly={!codeEditable}
          aria-describedby="system-code-hint"
        />
      </label>
      <p id="system-code-hint" className="form-hint">
        {codeEditable ? CODE_HINT : "Only an owner can change the System Code."}
      </p>
      <label>
        Description
        <textarea name="description" rows={3} maxLength={4000} defaultValue={initial?.description} />
      </label>
      <label>
        Business owner
        <input name="business_owner" maxLength={200} defaultValue={initial?.business_owner} />
      </label>
      <label>
        Technical owner
        <input name="technical_owner" maxLength={200} defaultValue={initial?.technical_owner} />
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
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">Systems</h3>
      {systems.length === 0 ? (
        <p className="empty-state">
          No Source Systems yet. Each Source System you add will get its own folder here.
        </p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">System Code</th>
              <th scope="col">Business owner</th>
              <th scope="col">Technical owner</th>
            </tr>
          </thead>
          <tbody>
            {systems.map((system) => (
              <tr key={system.id}>
                <td>
                  <button type="button" className="secondary" onClick={() => onOpen(system)}>
                    {system.name}
                  </button>
                </td>
                <td>{system.code}</td>
                <td>{system.business_owner || "—"}</td>
                <td>{system.technical_owner || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {allows(workspace, "source_system.create") ? (
        <AddSystemForm workspaceId={workspace.id} onAdded={onOpen} />
      ) : (
        <p>Only owners and editors can add Source Systems.</p>
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
    <form className="form" onSubmit={onSubmit} aria-label="Add Source System">
      <h4>Add a Source System</h4>
      <DetailFields codeEditable />
      {create.isError && (
        <p className="form-error" role="alert">
          {create.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={create.isPending}>
          Add Source System
        </button>
      </div>
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
      <section aria-labelledby="folder-title">
        <h3 id="folder-title">{system.name}</h3>
        <dl className="details">
          <dt>System Code</dt>
          <dd>{system.code}</dd>
          <dt>Description</dt>
          <dd>{system.description || "—"}</dd>
          <dt>Business owner</dt>
          <dd>{system.business_owner || "—"}</dd>
          <dt>Technical owner</dt>
          <dd>{system.technical_owner || "—"}</dd>
        </dl>
        <p>Only owners and editors can edit a Source System.</p>
      </section>
    );
  }

  const versionConflict = update.error instanceof ApiError && update.error.code === "version_conflict";

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate({ version: system.version, ...readFields(event.currentTarget) });
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form key={system.version} className="form" onSubmit={onSubmit} aria-labelledby="folder-title">
      <h3 id="folder-title">{system.name}</h3>
      <DetailFields initial={system} codeEditable={allows(workspace, "source_system.change_code")} />
      {versionConflict ? (
        <div className="form-error" role="alert">
          <p>
            Someone else changed this Source System since you opened it. Reload it to see their
            changes, then edit again.
          </p>
          <button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </button>
        </div>
      ) : (
        update.isError && (
          <p className="form-error" role="alert">
            {update.error.message}
          </p>
        )
      )}
      {update.isSuccess && <p role="status">Saved.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
      </div>
    </form>
  );
}
