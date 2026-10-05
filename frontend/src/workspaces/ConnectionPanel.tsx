"use client";

import { useState, type FormEvent } from "react";

import {
  useConnection,
  useSaveConnection,
  useTestConnection,
  type Connection,
  type ConnectionRequest,
} from "../api/connections";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";

function parseSchemas(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
}

/**
 * A Source System's live database Connection (spec stories 40-43): owners enter the
 * settings, test them before saving, choose the Database Schemas DAWAM may read, and are
 * warned when the database user can write. The saved password is never shown.
 */
export function ConnectionPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canManage = allows(workspace, "connection.manage");
  const connection = useConnection(workspace.id, system.id, canManage);

  if (!canManage) {
    return (
      <section aria-labelledby="folder-title">
        <h3 id="folder-title">Connection | Schema Import</h3>
        <p>Only owners can see or change a Source System&apos;s Connection.</p>
      </section>
    );
  }
  if (connection.isPending) {
    return <p role="status">Loading the Connection…</p>;
  }
  if (connection.isError) {
    return (
      <p role="alert">Could not load the Connection: {connection.error.message}</p>
    );
  }
  return (
    <ConnectionForm
      key={connection.data?.id ?? "new"}
      workspaceId={workspace.id}
      systemId={system.id}
      saved={connection.data}
    />
  );
}

function ConnectionForm({
  workspaceId,
  systemId,
  saved,
}: {
  workspaceId: string;
  systemId: string;
  saved: Connection | null;
}) {
  const save = useSaveConnection(workspaceId, systemId);
  const test = useTestConnection(workspaceId, systemId);
  const [schemas, setSchemas] = useState(
    (saved?.allowed_schemas ?? ["public"]).join(", "),
  );

  function read(form: HTMLFormElement): ConnectionRequest {
    const data = new FormData(form);
    const get = (name: string) => String(data.get(name) ?? "");
    const password = get("password");
    return {
      engine: "postgresql",
      host: get("host").trim(),
      port: Number(get("port")) || 5432,
      database: get("database").trim(),
      username: get("username").trim(),
      // Left blank, the stored password is kept (and used for a test).
      ...(password ? { password } : {}),
      options: saved?.options ?? {},
      allowed_schemas: parseSchemas(schemas),
    };
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    save.mutate(read(form), {
      // The saved password is never shown again: do not keep the typed one either.
      onSuccess: () => {
        (form.elements.namedItem("password") as HTMLInputElement).value = "";
      },
    });
  }

  const result = test.data;
  const writeWarning =
    (result?.ok ? result.can_write : undefined) ?? (test.data ? undefined : saved?.can_write);

  return (
    <form className="form" onSubmit={onSubmit} aria-label="Connection">
      <h3 id="folder-title">Connection | Schema Import</h3>
      <p className="form-hint">
        A PostgreSQL database DAWAM reads from. Give it a read-only user: DAWAM never writes.
      </p>
      <label>
        Host
        <input name="host" required maxLength={253} defaultValue={saved?.host} />
      </label>
      <label>
        Port
        <input
          name="port"
          type="number"
          min={1}
          max={65535}
          required
          defaultValue={saved?.port ?? 5432}
        />
      </label>
      <label>
        Database
        <input name="database" required maxLength={128} defaultValue={saved?.database} />
      </label>
      <label>
        Username
        <input
          name="username"
          required
          maxLength={128}
          autoComplete="off"
          defaultValue={saved?.username}
        />
      </label>
      <label>
        Password
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          placeholder={saved?.has_password ? "Saved. Leave blank to keep it." : ""}
        />
      </label>
      <label>
        Allowed Database Schemas
        <input
          name="allowed_schemas"
          value={schemas}
          onChange={(event) => setSchemas(event.target.value)}
          aria-describedby="allowed-schemas-hint"
        />
      </label>
      <p id="allowed-schemas-hint" className="form-hint">
        Comma separated. Nothing outside this list is ever read.
      </p>
      {result?.ok && result.available_schemas.length > 0 && (
        <p className="form-hint">
          Found on the server:{" "}
          {result.available_schemas.map((name) =>
            parseSchemas(schemas).includes(name) ? (
              <span key={name}>{name} </span>
            ) : (
              <button
                key={name}
                type="button"
                className="secondary"
                onClick={() => setSchemas([...parseSchemas(schemas), name].join(", "))}
              >
                Allow {name}
              </button>
            ),
          )}
        </p>
      )}

      {test.isError && (
        <p className="form-error" role="alert">
          {test.error.message}
        </p>
      )}
      {result && !result.ok && (
        <p className="form-error" role="alert">
          Connection failed: {result.error}
        </p>
      )}
      {result?.ok && (
        <p role="status">Connection works (PostgreSQL {result.server_version}).</p>
      )}
      {result?.ok && result.missing_schemas.length > 0 && (
        <p className="form-error" role="alert">
          These Database Schemas were not found: {result.missing_schemas.join(", ")}.
        </p>
      )}
      {writeWarning === true && (
        <p className="form-error" role="alert">
          Warning: this database user can write to the source. DAWAM only reads from it, so
          give it a read-only user.
        </p>
      )}

      {save.isError && (
        <p className="form-error" role="alert">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && <p role="status">Connection saved.</p>}
      <div className="form-actions">
        <button
          type="button"
          className="secondary"
          disabled={test.isPending}
          onClick={(event) => {
            const form = event.currentTarget.form;
            if (form?.reportValidity()) {
              test.mutate(read(form));
            }
          }}
        >
          Test connection
        </button>
        <button type="submit" disabled={save.isPending}>
          Save Connection
        </button>
      </div>
    </form>
  );
}
