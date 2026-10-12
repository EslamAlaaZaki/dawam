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
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { SchemaImportPanel } from "./SchemaImportPanel";
import {
  Actions,
  ErrorText,
  Hint,
  NativeSelect,
  OkText,
  Panel,
  formCardClasses,
  h3Classes,
  labelClasses,
  monoClasses,
} from "./parts";

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
  return (
    <div className="flex flex-col gap-6">
      <ConnectionSection workspace={workspace} system={system} />
      <SchemaImportPanel workspace={workspace} system={system} />
    </div>
  );
}

function ConnectionSection({
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
      <Panel aria-labelledby="folder-title">
        <h3 id="folder-title" className={h3Classes}>
          Connection | Schema Import
        </h3>
        <Hint>
          Only owners can see or change a Source System&apos;s Connection.
        </Hint>
      </Panel>
    );
  }
  if (connection.isPending) {
    return (
      <p role="status" className="text-sand-11">
        Loading the Connection…
      </p>
    );
  }
  if (connection.isError) {
    return (
      <ErrorText>
        Could not load the Connection: {connection.error.message}
      </ErrorText>
    );
  }
  return (
    <ConnectionForm
      // Stable across the first save, so the form keeps its state.
      key={system.id}
      workspaceId={workspace.id}
      systemId={system.id}
      saved={connection.data}
      readOnly={!canManage}
    />
  );
}

type Engine = Connection["engine"];

const ENGINES: Record<Engine, { label: string; port: number; schema: string }> =
  {
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
        toast.success("Connection saved.");
        (form.elements.namedItem("password") as HTMLInputElement).value = "";
      },
    });
  }

  const result = test.data;
  const writeWarning =
    (result?.ok ? result.can_write : undefined) ??
    (test.data ? undefined : saved?.can_write);

  return (
    <form
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-label="Connection"
    >
      <h3 id="folder-title" className={h3Classes}>
        Connection | Schema Import
      </h3>
      <Hint>
        A {ENGINES[engine].label} database DAWAM reads from. Give it a read-only
        user: DAWAM never writes.
      </Hint>
      {readOnly && (
        <p role="status">
          This Workspace is archived: its Connection is read-only.
        </p>
      )}
      <fieldset disabled={readOnly} className="flex min-w-0 flex-col gap-3.5">
        <label className={labelClasses}>
          Engine
          <NativeSelect
            name="engine"
            value={engine}
            onChange={(event) => chooseEngine(event.target.value as Engine)}
          >
            {(Object.keys(ENGINES) as Engine[]).map((key) => (
              <option key={key} value={key}>
                {ENGINES[key].label}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Host
          <Input
            name="host"
            required
            maxLength={253}
            defaultValue={saved?.host}
            className={monoClasses}
          />
        </label>
        <label className={labelClasses}>
          Port
          <Input
            name="port"
            className={monoClasses}
            type="number"
            min={1}
            max={65535}
            required
            value={port}
            onChange={(event) => setPort(event.target.value)}
          />
        </label>
        <label className={labelClasses}>
          Database
          <Input
            name="database"
            required
            maxLength={128}
            defaultValue={saved?.database}
            className={monoClasses}
          />
        </label>
        <label className={labelClasses}>
          Username
          <Input
            name="username"
            required
            maxLength={128}
            autoComplete="off"
            defaultValue={saved?.username}
            className={monoClasses}
          />
        </label>
        <label className={labelClasses}>
          Password
          <Input
            name="password"
            type="password"
            autoComplete="new-password"
            placeholder={
              saved?.has_password ? "Saved. Leave blank to keep it." : ""
            }
            aria-describedby="password-hint"
          />
        </label>
        {saved?.has_password && (
          <Hint id="password-hint">
            The saved password is only used with the saved host, port, database
            and username. Change any of them and enter the password again.
          </Hint>
        )}
        <label className={labelClasses}>
          Allowed Database Schemas
          <Input
            name="allowed_schemas"
            value={schemas}
            onChange={(event) => setSchemas(event.target.value)}
            aria-describedby="allowed-schemas-hint"
            className={monoClasses}
          />
        </label>
        <Hint id="allowed-schemas-hint">
          Comma separated. Nothing outside this list is ever read.
        </Hint>
      </fieldset>
      {result?.ok && result.available_schemas.length > 0 && (
        <Hint className="flex flex-wrap items-center gap-2">
          Found on the server:{" "}
          {result.available_schemas.map((name) =>
            parseSchemas(schemas).includes(name) ? (
              <span key={name} className={monoClasses}>
                {name}{" "}
              </span>
            ) : (
              <Button
                key={name}
                type="button"
                size="sm"
                onClick={() =>
                  setSchemas([...parseSchemas(schemas), name].join(", "))
                }
              >
                Allow {name}
              </Button>
            ),
          )}
        </Hint>
      )}

      {test.isError && <ErrorText>{test.error.message}</ErrorText>}
      {result && !result.ok && (
        <ErrorText>Connection failed: {result.error}</ErrorText>
      )}
      {result?.ok && (
        <OkText role="status">
          Connection works ({ENGINES[engine].label} {result.server_version}).
        </OkText>
      )}
      {result?.ok && result.missing_schemas.length > 0 && (
        <ErrorText>
          These Database Schemas were not found:{" "}
          {result.missing_schemas.join(", ")}.
        </ErrorText>
      )}
      {writeWarning === true && (
        <ErrorText>
          Warning: this database user can write to the source. DAWAM only reads
          from it, so give it a read-only user.
        </ErrorText>
      )}

      {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      {!readOnly && (
        <Actions>
          <Button
            type="button"
            disabled={test.isPending}
            onClick={(event) => {
              const form = event.currentTarget.form;
              if (form?.reportValidity()) {
                test.mutate(read(form));
              }
            }}
          >
            Test connection
          </Button>
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save Connection
          </Button>
        </Actions>
      )}
    </form>
  );
}
