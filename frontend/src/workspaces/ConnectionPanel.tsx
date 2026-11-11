"use client";

import {
  CircleCheck,
  CircleX,
  FileSpreadsheet,
  Lock,
  Plug,
  Plus,
  TriangleAlert,
  X,
} from "lucide-react";
import {
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  useConnection,
  useSaveConnection,
  useTestConnection,
  type Connection,
  type ConnectionRequest,
} from "../api/connections";
import { useImportStatus } from "../api/profiling";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { cn } from "../lib/utils";
import { SchemaImportPanel } from "./SchemaImportPanel";
import {
  Callout,
  ErrorText,
  Hint,
  NativeSelect,
  labelClasses,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

function parseSchemas(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
}

type Tab = "connection" | "import";
const TAB_KEY = "dawam.connection-tab";

// Browser storage can be missing or blocked: the tab is then simply not remembered.
function lastTab(): Tab {
  try {
    return localStorage.getItem(TAB_KEY) === "import" ? "import" : "connection";
  } catch {
    return "connection";
  }
}

function rememberTab(tab: Tab) {
  try {
    localStorage.setItem(TAB_KEY, tab);
  } catch {
    // Not remembered.
  }
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

/**
 * A Source System's live database Connection (spec stories 40-43) and its Schema Import,
 * as two methods to choose from. Owners enter the settings, test them before saving,
 * choose the Database Schemas DAWAM may read, and are warned when the database user can
 * write. The saved password is never shown. In an archived Workspace owners can still see
 * the Connection, read-only. The method used last is remembered.
 */
export function ConnectionPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canImport = allows(workspace, "source_system.extract");
  const canView = allows(workspace, "connection.view");
  const [stored, setStored] = useState<Tab>(lastTab);
  const tab: Tab = stored === "import" && canImport ? "import" : "connection";
  const connection = useConnection(workspace.id, system.id, canView);
  const importStatus = useImportStatus(workspace.id, system.id);
  const saved = canView ? connection.data : null;
  const hasConnection = canView
    ? Boolean(saved)
    : importStatus.data?.has_connection === true;
  const inUse: Tab | null = hasConnection
    ? "connection"
    : importStatus.data?.imported
      ? "import"
      : null;

  function choose(next: Tab) {
    setStored(next);
    rememberTab(next);
  }

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-5">
      <header className="flex flex-col gap-2 border-b border-border pb-5">
        <h1
          id="folder-title"
          className="text-2xl font-semibold tracking-tight text-balance text-foreground"
        >
          Connection
        </h1>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {inUse === "connection" ? (
            <>
              <Badge variant="success" dot>
                Live connection
              </Badge>
              {saved && (
                <>
                  <span className={monoClasses}>
                    {ENGINES[saved.engine].label} · {saved.host}:{saved.port}/
                    {saved.database}
                  </span>
                  <span>
                    · user <span className={monoClasses}>{saved.username}</span>
                  </span>
                </>
              )}
            </>
          ) : inUse === "import" ? (
            <Badge variant="warning" dot>
              Schema Import
            </Badge>
          ) : (
            <Badge dot>Not set up</Badge>
          )}
        </div>
      </header>
      {canImport && (
        <div
          role="group"
          aria-label="How DAWAM gets this system's metadata"
          className="grid grid-cols-1 gap-3 sm:grid-cols-2"
        >
          <MethodCard
            selected={tab === "connection"}
            inUse={inUse === "connection"}
            icon={<Plug aria-hidden />}
            title="Live connection"
            onClick={() => choose("connection")}
          >
            DAWAM reads the database directly with a read-only user.
          </MethodCard>
          <MethodCard
            selected={tab === "import"}
            inUse={inUse === "import"}
            icon={<FileSpreadsheet aria-hidden />}
            title="Import from template"
            onClick={() => choose("import")}
          >
            For databases DAWAM can&apos;t reach: run our queries, fill the
            template, upload it.
          </MethodCard>
        </div>
      )}
      {/* Both stay mounted so a half-filled form survives a switch. */}
      <div hidden={tab !== "connection"}>
        <ConnectionSection workspace={workspace} system={system} />
      </div>
      {canImport && (
        <div hidden={tab !== "import"}>
          <SchemaImportPanel workspace={workspace} system={system} />
        </div>
      )}
    </section>
  );
}

function MethodCard({
  selected,
  inUse,
  icon,
  title,
  onClick,
  children,
}: {
  selected: boolean;
  inUse: boolean;
  icon: ReactNode;
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-pill border p-4 text-left transition-colors",
        selected
          ? "border-primary bg-primary-subtle"
          : "border-border bg-surface-raised hover:border-border-strong",
      )}
    >
      <span
        className={cn(
          "grid size-8 shrink-0 place-items-center rounded-md [&_svg]:size-4",
          selected
            ? "bg-surface-raised text-primary"
            : "bg-muted text-muted-foreground",
        )}
      >
        {icon}
      </span>
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
          {title}
          {inUse && <Badge variant="success">In use</Badge>}
        </span>
        <span className="text-sm text-muted-foreground">{children}</span>
      </span>
    </button>
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
      <Callout icon={<Lock aria-hidden />}>
        <Hint>
          Only owners can see or change a Source System&apos;s Connection.
        </Hint>
      </Callout>
    );
  }
  if (connection.isPending) {
    return (
      <Loading label="Loading the Connection…" className="p-0" />
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
  const [schemas, setSchemas] = useState<string[]>(
    saved?.allowed_schemas ?? parseSchemas(ENGINES[engine].schema),
  );
  // A schema typed but not yet added with Enter or a comma.
  const [draft, setDraft] = useState("");

  // Switching engine moves the port and schema that are still the previous engine's default.
  function chooseEngine(next: Engine) {
    const previous = ENGINES[engine];
    if (port === String(previous.port)) setPort(String(ENGINES[next].port));
    if (schemas.join(", ") === previous.schema) {
      setSchemas(parseSchemas(ENGINES[next].schema));
    }
    setEngine(next);
  }

  function addSchemas(text: string) {
    const added = parseSchemas(text).filter((name) => !schemas.includes(name));
    if (added.length > 0) setSchemas([...schemas, ...new Set(added)]);
  }

  function onDraftKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addSchemas(draft);
      setDraft("");
    } else if (event.key === "Backspace" && !draft && schemas.length > 0) {
      setSchemas(schemas.slice(0, -1));
    }
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
      allowed_schemas: [...new Set([...schemas, ...parseSchemas(draft)])],
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
  const notAllowed = result?.ok
    ? result.available_schemas.filter((name) => !schemas.includes(name))
    : [];

  return (
    <form
      className="flex flex-col rounded-pill border border-border bg-surface-raised"
      onSubmit={onSubmit}
      aria-label="Connection"
    >
      <div className="flex flex-col gap-4 p-5">
        {readOnly && (
          <Callout icon={<Lock aria-hidden />} role="status">
            This Workspace is archived: its Connection is read-only.
          </Callout>
        )}
        <Hint className="max-w-prose">
          Use this when DAWAM can reach the database: a live, read-only
          connection to a {ENGINES[engine].label} database. Give it a read-only
          user: DAWAM never writes.
        </Hint>
        <fieldset
          disabled={readOnly}
          className="m-0 flex min-w-0 flex-col gap-4 border-0 p-0"
        >
          <div className="flex max-w-xs flex-col gap-1.5">
            <label className={labelClasses}>
              Engine
              <NativeSelect
                name="engine"
                value={engine}
                aria-describedby="engine-hint"
                onChange={(event) => chooseEngine(event.target.value as Engine)}
              >
                {(Object.keys(ENGINES) as Engine[]).map((key) => (
                  <option key={key} value={key}>
                    {ENGINES[key].label}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <p id="engine-hint" className="text-xs text-subtle-foreground">
              Picking an engine fills in its default port.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-[minmax(0,1fr)_7.5rem]">
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
          </div>
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            <div className="flex min-w-0 flex-col gap-1.5">
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
                <p
                  id="password-hint"
                  className="text-xs text-subtle-foreground"
                >
                  The saved password is only used with the saved host, port,
                  database and username. Change any of them and enter the
                  password again.
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <span id="allowed-schemas-label" className="text-sm font-medium">
              Allowed Database Schemas
            </span>
            <div
              role="group"
              aria-labelledby="allowed-schemas-label"
              aria-describedby="allowed-schemas-hint"
              className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-control-edge bg-background px-1.5 py-1 focus-within:border-primary"
            >
              {schemas.map((name) => (
                <span
                  key={name}
                  className="inline-flex h-6 items-center gap-1 rounded-md bg-muted pr-1 pl-2 font-mono text-mono"
                >
                  {name}
                  <button
                    type="button"
                    aria-label={`Remove ${name}`}
                    onClick={() =>
                      setSchemas(schemas.filter((other) => other !== name))
                    }
                    className="grid size-4.5 cursor-pointer place-items-center rounded-sm text-subtle-foreground hover:bg-border hover:text-foreground disabled:cursor-not-allowed [&_svg]:size-3"
                  >
                    <X aria-hidden />
                  </button>
                </span>
              ))}
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={onDraftKey}
                onBlur={() => {
                  addSchemas(draft);
                  setDraft("");
                }}
                placeholder="Add a schema, press Enter"
                aria-label="Add a Database Schema"
                className="h-6 min-w-32 flex-1 bg-transparent font-mono text-mono outline-none placeholder:text-subtle-foreground disabled:cursor-not-allowed"
              />
            </div>
            <input type="hidden" name="allowed_schemas" value={schemas.join(", ")} />
            <p
              id="allowed-schemas-hint"
              className="text-xs text-subtle-foreground"
            >
              Nothing outside this list is ever read.
            </p>
          </div>
        </fieldset>

        {test.isError && <ErrorText>{test.error.message}</ErrorText>}
        {result && !result.ok && (
          <Callout tone="danger" icon={<CircleX aria-hidden />}>
            <p role="alert" className="text-danger-strong">
              Connection failed: {result.error}
            </p>
          </Callout>
        )}
        {result?.ok && (
          <Callout tone="success" icon={<CircleCheck aria-hidden />} role="status">
            <span className="font-medium">
              Connection works ({ENGINES[engine].label} {result.server_version}).
            </span>
            {notAllowed.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <span>Also found on the server:</span>
                {notAllowed.map((name) => (
                  <button
                    key={name}
                    type="button"
                    disabled={readOnly}
                    aria-label={`Allow ${name}`}
                    onClick={() => setSchemas([...schemas, name])}
                    className="inline-flex h-6 cursor-pointer items-center gap-1 rounded-md border border-dashed border-border-strong px-2 font-mono text-mono text-muted-foreground hover:border-primary hover:text-primary disabled:cursor-not-allowed [&_svg]:size-3"
                  >
                    <Plus aria-hidden />
                    {name}
                  </button>
                ))}
              </div>
            ) : (
              result.available_schemas.length > 0 && (
                <span>All schemas found on the server are allowed.</span>
              )
            )}
          </Callout>
        )}
        {result?.ok && result.missing_schemas.length > 0 && (
          <ErrorText>
            These Database Schemas were not found:{" "}
            {result.missing_schemas.join(", ")}.
          </ErrorText>
        )}
        {writeWarning === true && (
          <Callout
            tone="warning"
            icon={<TriangleAlert aria-hidden />}
            role="status"
          >
            Warning: this database user can write to the source. DAWAM only
            reads from it, so give it a read-only user.
          </Callout>
        )}
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      </div>
      {!readOnly && (
        <div className="flex flex-wrap justify-end gap-2 rounded-b-pill border-t border-border bg-surface px-5 py-3">
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
            <Plug aria-hidden />
            Test connection
          </Button>
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save connection
          </Button>
        </div>
      )}
    </form>
  );
}
