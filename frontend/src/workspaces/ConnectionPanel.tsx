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
 * warned when the database user can write. The saved password is never shown. In an
 * archived Workspace owners can still see the Connection, read-only.
 */
export function ConnectionPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canView = allows(workspace, "connection.view");
  const canManage = allows(workspace, "connection.manage");
  const connection = useConnection(workspace.id, system.id, canView);

  if (!canView) {
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
      readOnly={!canManage}
    />
  );
}

type Engine = Connection["engine"];

const ENGINES: Record<Engine, { label: string; port: number; schema: string }> = {
  postgresql: { label: "PostgreSQL", port: 5432, schema: "public" },
  sqlserver: { label: "SQL Server", port: 1433, schema: "dbo" },
  // A MySQL schema is a database: the owner names it.
  mysql: { label: "MySQL / MariaDB", port: 3306, schema: "" },
  // An Oracle schema is a user name, as Oracle stores it (usually upper case): the owner names it.
  oracle: { label: "Oracle", port: 1521, schema: "" },
};

function ConnectionForm({
  workspaceId,
  systemId,
  saved,
  readOnly,
}: {
  workspaceId: string;
  systemId: string;
  saved: Connection | null;
  readOnly: boolean;
}) {
  const save = useSaveConnection(workspaceId, systemId);
  const test = useTestConnection(workspaceId, systemId);
  const [engine, setEngine] = useState<Engine>(saved?.engine ?? "postgresql");
  const [port, setPort] = useState(String(saved?.port ?? ENGINES[engine].port));
  const [schemas, setSchemas] = useState(
    (saved?.allowed_schemas ?? [ENGINES[engine].schema]).join(", "),
  );

  // Switching engine moves the port and schema that are still the previous engine's default.
  function chooseEngine(next: Engine) {
    const previous = ENGINES[engine];
    if (port === String(previous.port)) setPort(String(ENGINES[next].port));
    if (schemas.trim() === previous.schema) setSchemas(ENGINES[next].schema);
    setEngine(next);
  }

  function read(form: HTMLFormElement): ConnectionRequest {
    const data = new FormData(form);
    const get = (name: string) => String(data.get(name) ?? "");
    const password = get("password");
    return {
      engine,
      host: get("host").trim(),
      port: Number(get("port")) || ENGINES[engine].port,
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
        A {ENGINES[engine].label} database DAWAM reads from. Give it a read-only user: DAWAM
        never writes.
      </p>
      {readOnly && (
        <p role="status">This Workspace is archived: its Connection is read-only.</p>
      )}
      <fieldset disabled={readOnly} className="form">
        <label>
          Engine
          <select
            name="engine"
            value={engine}
            onChange={(event) => chooseEngine(event.target.value as Engine)}
          >
            {(Object.keys(ENGINES) as Engine[]).map((key) => (
              <option key={key} value={key}>
                {ENGINES[key].label}
              </option>
            ))}
          </select>
        </label>
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
            value={port}
            onChange={(event) => setPort(event.target.value)}
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
            aria-describedby="password-hint"
          />
        </label>
        {saved?.has_password && (
          <p id="password-hint" className="form-hint">
            The saved password is only used with the saved host, port, database and username.
            Change any of them and enter the password again.
          </p>
        )}
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
      </fieldset>
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
        <p role="status">Connection works ({ENGINES[engine].label} {result.server_version}).</p>
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
      {!readOnly && (
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
      )}
    </form>
  );
}
