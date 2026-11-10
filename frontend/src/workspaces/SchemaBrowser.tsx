"use client";

import {
  ArrowRight,
  ChevronDown,
  Code,
  Eye,
  Folder,
  Parentheses,
  RefreshCw,
  Search,
  SquareFunction,
  Table2,
  Workflow,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import {
  useImportStatus,
  useStartProfiling,
  useTableProfile,
  type ProfilingRun,
} from "../api/profiling";
import {
  useSchemaSearch,
  useSourceSchema,
  type SchemaSearchHit,
  type SourceSchema,
} from "../api/snapshots";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { cn } from "../lib/utils";
import { ProfilePanel, ProtectedIcon } from "./ProfilePanel";
import {
  EmptyState,
  ErrorText,
  TableCard,
  h5Classes,
  linkButtonClasses,
  listCardClasses,
  monoClasses,
  preClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

type SchemaTable = SourceSchema["tables"][number];
type RemovedTable = SourceSchema["removed_tables"][number];
type Routine = SourceSchema["routines"][number];
type RemovedColumn = SourceSchema["removed_columns"][number];
type Selection =
  { kind: "table"; id: string } | { kind: "routine"; id: string } | null;

const STATUS_LABELS: Record<string, string> = {
  source_removed: "removed from the source",
  out_of_scope: "out of scope",
  deleted: "deleted",
};

/** Flags an object that is not `present` (a present one needs no flag). */
function StatusBadge({ status }: { status?: string | null }) {
  if (!status || status === "present") {
    return null;
  }
  return (
    <Badge variant="warning" className="ml-2 shrink-0">
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

function useDebounced(value: string, ms: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

/** A row of an object list: the object, then what kind it is. */
const rowClasses = "flex min-w-0 items-center gap-1 px-3 py-1.5 hover:bg-muted";

/** The small dot between the facts of a line. */
export function Sep() {
  return <span aria-hidden className="size-1 rounded-full bg-border-strong" />;
}

/** A label/value pair in the table header; a key count wears its Badge variant. */
function Stat({
  label,
  badge,
  children,
}: {
  label: string;
  badge?: "pk" | "fk";
  children: ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <dt className="text-xs font-medium text-subtle-foreground">
        {badge ? <Badge variant={badge}>{label}</Badge> : label}
      </dt>
      <dd className="font-medium text-foreground">{children}</dd>
    </div>
  );
}

/** Column names as small muted chips. */
function ColumnChips({ columns }: { columns: string[] }) {
  return (
    <span className={`${monoClasses} inline-flex flex-wrap gap-1`}>
      {columns.map((c) => (
        <span
          key={c}
          className="rounded-md bg-muted px-1.5 text-xs text-muted-foreground"
        >
          {c}
        </span>
      ))}
    </span>
  );
}

/** A long name cut with an ellipsis; the full name shows in a tooltip. */
function Truncated({ text, className }: { text: string; className?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={`inline-block min-w-0 max-w-full truncate align-bottom ${className ?? ""}`}
        >
          {text}
        </span>
      </TooltipTrigger>
      <TooltipContent>{text}</TooltipContent>
    </Tooltip>
  );
}

type ProfileState = "profiled" | "none" | "running";

const PROFILE_DOT: Record<ProfileState, string> = {
  profiled: "bg-success",
  none: "bg-border-strong",
  running: "bg-info",
};
const PROFILE_LABEL: Record<ProfileState, string> = {
  profiled: "Profiled",
  none: "Not profiled",
  running: "Profiling",
};

function profileState(table: SchemaTable, profiling: Set<string>) {
  if (profiling.has(table.id)) {
    return "running";
  }
  return table.profiled_at != null ? "profiled" : "none";
}

/** Whether a table's profile is current, missing or being made, as a coloured dot. */
function ProfileDot({ state }: { state: ProfileState }) {
  return (
    <span
      role="img"
      aria-label={PROFILE_LABEL[state]}
      title={PROFILE_LABEL[state]}
      className={cn("size-1.5 shrink-0 rounded-full", PROFILE_DOT[state])}
    />
  );
}

/** The tree's indent per level, in the spacing scale. */
const INDENT = ["ps-2", "ps-6", "ps-10", "ps-14"] as const;

const nodeClasses =
  "flex h-7 w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md pe-2 text-start text-sm transition-colors hover:bg-muted [&_svg]:size-4 [&_svg]:shrink-0";

/** A folder of the tree: it opens and closes, and counts what it holds. */
function TreeGroup({
  label,
  icon,
  count,
  level,
  open,
  onToggle,
  listLabel,
  strong = false,
  badge,
  children,
}: {
  label: ReactNode;
  icon: ReactNode;
  count: number;
  level: number;
  open: boolean;
  onToggle: () => void;
  listLabel?: string;
  strong?: boolean;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className={cn(nodeClasses, INDENT[level])}
      >
        <ChevronDown
          aria-hidden
          className={cn(
            "text-subtle-foreground transition-transform",
            !open && "-rotate-90",
          )}
        />
        <span aria-hidden className="text-subtle-foreground">
          {icon}
        </span>
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            strong
              ? `${monoClasses} font-semibold text-foreground`
              : "text-xs font-medium text-muted-foreground",
          )}
        >
          {label}
        </span>
        {badge}
        <span className="text-xs text-subtle-foreground tabular-nums">
          {count}
        </span>
      </button>
      {open && (
        <ul aria-label={listLabel} className="flex flex-col gap-px">
          {count === 0 ? (
            <li
              className={cn(
                "py-1 text-xs text-subtle-foreground",
                INDENT[Math.min(level + 1, 3)],
                "ms-5",
              )}
            >
              None
            </li>
          ) : (
            children
          )}
        </ul>
      )}
    </li>
  );
}

/** A table, view or routine of the tree: it opens the object's page. */
function TreeLeaf({
  name,
  title,
  icon,
  level,
  selected,
  onOpen,
  after,
}: {
  name: string;
  title: string;
  icon: ReactNode;
  level: number;
  selected: boolean;
  onOpen: () => void;
  after?: ReactNode;
}) {
  return (
    <li
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-md pe-2 transition-colors",
        selected ? "bg-primary-subtle text-primary" : "hover:bg-muted",
      )}
    >
      <button
        type="button"
        aria-current={selected || undefined}
        title={title}
        onClick={onOpen}
        className={cn(
          "flex h-7 min-w-0 flex-1 cursor-pointer items-center gap-1.5 text-start [&_svg]:size-4 [&_svg]:shrink-0",
          INDENT[level],
          "ms-5",
        )}
      >
        <span
          aria-hidden
          className={selected ? "text-primary" : "text-subtle-foreground"}
        >
          {icon}
        </span>
        <span className={`${monoClasses} min-w-0 truncate`}>{name}</span>
      </button>
      {after}
    </li>
  );
}

/** A table or view the latest Snapshot no longer has: listed and flagged, not opened. */
function RemovedLeaf({ table, level }: { table: RemovedTable; level: number }) {
  return (
    <li
      className={cn(
        "flex h-7 min-w-0 items-center gap-1.5 pe-2 text-muted-foreground [&_svg]:size-4 [&_svg]:shrink-0",
        INDENT[level],
        "ms-5",
      )}
    >
      <span aria-hidden className="text-subtle-foreground">
        {table.kind === "view" ? <Eye /> : <Table2 />}
      </span>
      <Truncated text={table.name} className={monoClasses} />
      <StatusBadge status={table.status} />
    </li>
  );
}

/**
 * Browses a Source System's Source Schema (spec story 54): a tree of Database Schemas with
 * their tables, views, functions and procedures, a page per table or routine, and a search
 * by name. Any member can browse. Objects that are not `present` carry their state; each
 * table and view shows whether it is profiled.
 */
export function SchemaBrowser({
  workspace,
  systemId,
  profiling,
  onProfilingStarted,
}: {
  workspace: Workspace;
  systemId: string;
  /** The tables a job started from the page is profiling now. */
  profiling: Set<string>;
  onProfilingStarted: (run: ProfilingRun) => void;
}) {
  const workspaceId = workspace.id;
  const schema = useSourceSchema(workspaceId, systemId, true);
  const [selection, setSelection] = useState<Selection>(null);
  const [search, setSearch] = useState("");
  const [closed, setClosed] = useState<Set<string>>(() => new Set());
  const query = useDebounced(search.trim(), 250);
  const results = useSchemaSearch(workspaceId, systemId, query);

  if (schema.isPending) {
    return <Loading label="Loading the Source Schema…" className="p-0" />;
  }
  if (schema.isError) {
    return (
      <ErrorText>
        Could not load the Source Schema: {schema.error.message}
      </ErrorText>
    );
  }
  const content = schema.data;

  function open(hit: SchemaSearchHit) {
    if (hit.kind === "routine") {
      setSelection({ kind: "routine", id: hit.id });
      return;
    }
    const table =
      hit.kind === "column"
        ? content.tables.find(
            (t) => t.db_schema === hit.db_schema && t.name === hit.table,
          )
        : content.tables.find((t) => t.id === hit.id);
    if (table) {
      setSelection({ kind: "table", id: table.id });
    }
  }

  const isOpen = (key: string) => !closed.has(key);
  const toggle = (key: string) =>
    setClosed((current) => {
      const next = new Set(current);
      if (!next.delete(key)) {
        next.add(key);
      }
      return next;
    });

  const table =
    selection?.kind === "table"
      ? content.tables.find((t) => t.id === selection.id)
      : undefined;
  const routine =
    selection?.kind === "routine"
      ? content.routines.find((r) => r.id === selection.id)
      : undefined;

  function tableLeaf(t: SchemaTable) {
    return (
      <TreeLeaf
        key={t.id}
        name={t.name}
        title={t.name}
        icon={t.kind === "view" ? <Eye /> : <Table2 />}
        level={2}
        selected={selection?.kind === "table" && selection.id === t.id}
        onOpen={() => setSelection({ kind: "table", id: t.id })}
        after={
          <>
            <StatusBadge status={t.status} />
            <ProfileDot state={profileState(t, profiling)} />
          </>
        }
      />
    );
  }

  function routineLeaf(r: Routine) {
    const label = `${r.name}(${r.signature})`;
    return (
      <TreeLeaf
        key={r.id}
        name={label}
        title={label}
        icon={r.kind === "function" ? <SquareFunction /> : <Workflow />}
        level={3}
        selected={selection?.kind === "routine" && selection.id === r.id}
        onOpen={() => setSelection({ kind: "routine", id: r.id })}
        after={<StatusBadge status={r.status} />}
      />
    );
  }

  return (
    <div className="grid overflow-hidden rounded-pill border border-border lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col border-b border-border bg-surface lg:border-e lg:border-b-0">
        <div className="flex flex-col gap-3 border-b border-border p-3">
          <label htmlFor="schema-search" className="sr-only">
            Search by name
          </label>
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute start-2.5 top-2 size-4 text-subtle-foreground"
            />
            <Input
              id="schema-search"
              type="search"
              value={search}
              placeholder="A table, column, routine or Database Schema"
              onChange={(event) => setSearch(event.target.value)}
              className="ps-8"
            />
          </div>
          {query !== "" && (
            <SearchResults query={query} results={results} onOpen={open} />
          )}
        </div>
        <ul
          aria-label="Database Schemas"
          className="flex flex-1 flex-col gap-px overflow-auto p-2"
        >
          {content.db_schemas.map((dbSchema) => {
            const name = dbSchema.name;
            const ofSchema = <T extends { db_schema: string }>(items: T[]) =>
              items.filter((item) => item.db_schema === name);
            const tables = ofSchema(content.tables);
            const removed = ofSchema(content.removed_tables);
            const routines = ofSchema(content.routines);
            const kind = <T extends { kind: string }>(items: T[], k: string) =>
              items.filter((item) => item.kind === k);
            const functions = kind(routines, "function");
            const procedures = kind(routines, "procedure");
            const tableCount =
              kind(tables, "table").length + kind(removed, "table").length;
            const viewCount =
              kind(tables, "view").length + kind(removed, "view").length;
            return (
              <TreeGroup
                key={dbSchema.id}
                label={name}
                icon={<Folder />}
                count={tables.length + removed.length + routines.length}
                level={0}
                strong
                badge={<StatusBadge status={dbSchema.status} />}
                open={isOpen(name)}
                onToggle={() => toggle(name)}
              >
                <TreeGroup
                  label="Tables"
                  icon={<Table2 />}
                  count={tableCount}
                  level={1}
                  listLabel={`Tables of ${name}`}
                  open={isOpen(`${name}/tables`)}
                  onToggle={() => toggle(`${name}/tables`)}
                >
                  {kind(tables, "table").map(tableLeaf)}
                  {kind(removed, "table").map((t) => (
                    <RemovedLeaf key={t.id} table={t} level={2} />
                  ))}
                </TreeGroup>
                <TreeGroup
                  label="Views"
                  icon={<Eye />}
                  count={viewCount}
                  level={1}
                  listLabel={`Views of ${name}`}
                  open={isOpen(`${name}/views`)}
                  onToggle={() => toggle(`${name}/views`)}
                >
                  {kind(tables, "view").map(tableLeaf)}
                  {kind(removed, "view").map((t) => (
                    <RemovedLeaf key={t.id} table={t} level={2} />
                  ))}
                </TreeGroup>
                <TreeGroup
                  label="Programmability"
                  icon={<Code />}
                  count={routines.length}
                  level={1}
                  listLabel={`Routines of ${name}`}
                  open={isOpen(`${name}/routines`)}
                  onToggle={() => toggle(`${name}/routines`)}
                >
                  <TreeGroup
                    label="Functions"
                    icon={<SquareFunction />}
                    count={functions.length}
                    level={2}
                    listLabel={`Functions of ${name}`}
                    open={isOpen(`${name}/functions`)}
                    onToggle={() => toggle(`${name}/functions`)}
                  >
                    {functions.map(routineLeaf)}
                  </TreeGroup>
                  <TreeGroup
                    label="Procedures"
                    icon={<Parentheses />}
                    count={procedures.length}
                    level={2}
                    listLabel={`Procedures of ${name}`}
                    open={isOpen(`${name}/procedures`)}
                    onToggle={() => toggle(`${name}/procedures`)}
                  >
                    {procedures.map(routineLeaf)}
                  </TreeGroup>
                </TreeGroup>
              </TreeGroup>
            );
          })}
        </ul>
        <ul
          aria-label="Profile legend"
          className="flex flex-wrap gap-x-3 gap-y-1 border-t border-border px-4 pt-2 pb-3 text-xs text-subtle-foreground"
        >
          {(["profiled", "none", "running"] as const).map((state) => (
            <li key={state} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className={cn("size-1.5 rounded-full", PROFILE_DOT[state])}
              />
              {PROFILE_LABEL[state]}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex min-w-0 flex-col bg-surface-raised">
        {table && (
          <TablePage
            table={table}
            workspace={workspace}
            systemId={systemId}
            running={profiling.has(table.id)}
            onProfilingStarted={onProfilingStarted}
            removedColumns={content.removed_columns.filter(
              (c) => c.table_id === table.id,
            )}
          />
        )}
        {routine && <RoutinePage routine={routine} />}
        {!table && !routine && (
          <EmptyState className="m-5">
            Open a table, view or routine from the tree.
          </EmptyState>
        )}
      </div>
    </div>
  );
}

function SearchResults({
  query,
  results,
  onOpen,
}: {
  query: string;
  results: ReturnType<typeof useSchemaSearch>;
  onOpen: (hit: SchemaSearchHit) => void;
}) {
  if (results.isPending) {
    return <p className="text-muted-foreground">Searching…</p>;
  }
  if (results.isError) {
    return <ErrorText>Could not search: {results.error.message}</ErrorText>;
  }
  if (results.data.length === 0) {
    return <EmptyState>Nothing is named like “{query}”.</EmptyState>;
  }
  return (
    <ul aria-label="Search results" className={listCardClasses}>
      {results.data.map((hit) => (
        <li key={`${hit.kind}-${hit.id}`} className={rowClasses}>
          <button
            type="button"
            className={`${linkButtonClasses} min-w-0 truncate`}
            onClick={() => onOpen(hit)}
          >
            {[hit.db_schema, hit.table, hit.name].filter(Boolean).join(".")}
          </button>
          {` (${hit.kind === "db_schema" ? "Database Schema" : hit.kind})`}
          <StatusBadge status={hit.status} />
        </li>
      ))}
    </ul>
  );
}

/** Whether a column is part of a foreign key (derived from the keys shown below). */
function isFkColumn(table: SchemaTable, name: string) {
  return table.constraints.some(
    (k) => k.type === "fk" && k.columns.includes(name),
  );
}

/** An object page's header: the mono `schema.name`, its kind and its facts. */
function ObjectHeader({
  dbSchema,
  name,
  kind,
  status,
  facts,
  actions,
}: {
  dbSchema: string;
  name: string;
  kind: string;
  status?: string | null;
  facts?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start gap-3 border-b border-border px-5 py-4">
      <div className="flex min-w-0 flex-1 basis-64 flex-col gap-1">
        <h2 className="flex min-w-0 flex-wrap items-center gap-2 font-mono text-xl font-semibold break-all text-foreground">
          <span>
            <span className="font-medium text-subtle-foreground">
              {dbSchema}.
            </span>
            {name}
          </span>
          <Badge>{kind}</Badge>
          <StatusBadge status={status} />
        </h2>
        {facts}
      </div>
      {actions}
    </div>
  );
}

/**
 * When the table was last profiled, and for owners and editors a button that profiles it
 * again; it spins while the table's job runs.
 */
function ProfileControl({
  workspace,
  systemId,
  table,
  running,
  onProfilingStarted,
  onError,
}: {
  workspace: Workspace;
  systemId: string;
  table: SchemaTable;
  running: boolean;
  onProfilingStarted: (run: ProfilingRun) => void;
  onError: (message: string | null) => void;
}) {
  const start = useStartProfiling(workspace.id, systemId);
  const importStatus = useImportStatus(workspace.id, systemId);
  const unavailable =
    importStatus.data?.imported === true && !importStatus.data.has_connection;
  const canProfile =
    allows(workspace, "source_system.profile") &&
    importStatus.isSuccess &&
    !unavailable;
  const busy = running || start.isPending;
  const state: ProfileState = running
    ? "running"
    : table.profiled_at != null
      ? "profiled"
      : "none";
  const action =
    table.profiled_at != null ? "Profile again" : "Profile this table";

  return (
    <div className="flex h-8 items-center overflow-hidden rounded-md border border-border-strong bg-surface-raised">
      <span
        role="status"
        className="flex h-full items-center gap-1.5 px-2.5 text-xs whitespace-nowrap text-muted-foreground tabular-nums"
      >
        {state === "running" ? (
          <Badge variant="info">Profiling…</Badge>
        ) : (
          <>
            <span
              aria-hidden
              className={cn("size-1.5 rounded-full", PROFILE_DOT[state])}
            />
            {table.profiled_at != null
              ? `Profiled ${new Date(table.profiled_at).toLocaleString()}`
              : "Not profiled yet"}
          </>
        )}
      </span>
      {canProfile && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={action}
          title={action}
          disabled={busy}
          className="h-full rounded-none border-0 border-s border-border-strong"
          onClick={() => {
            onError(null);
            start.mutate([table.id], {
              onSuccess: (job) => {
                onProfilingStarted({ jobId: job.job_id, tableIds: [table.id] });
                toast.success(
                  "Profiling started. Follow it under Background jobs.",
                );
              },
              onError: (error) => onError(error.message),
            });
          }}
        >
          <RefreshCw
            aria-hidden
            className={cn(busy && "motion-safe:animate-spin")}
          />
        </Button>
      )}
    </div>
  );
}

function TablePage({
  table,
  workspace,
  systemId,
  running,
  onProfilingStarted,
  removedColumns,
}: {
  table: SchemaTable;
  workspace: Workspace;
  systemId: string;
  running: boolean;
  onProfilingStarted: (run: ProfilingRun) => void;
  removedColumns: RemovedColumn[];
}) {
  const [tab, setTab] = useState("columns");
  const [error, setError] = useState<string | null>(null);
  // The Source Schema carries no protection flag; the table's profile (shared with the
  // Profile tab) does.
  const columnProfiles = useTableProfile(workspace.id, systemId, table.id);
  const protectedNames = new Set(
    columnProfiles.data?.columns
      .filter((c) => c.is_protected)
      .map((c) => c.name),
  );
  const title = `${table.db_schema}.${table.name}`;
  const pks = table.constraints.filter((k) => k.type === "pk").length;
  const fks = table.constraints.filter((k) => k.type === "fk").length;
  const keyCount = table.constraints.length + table.indexes.length;
  return (
    <section aria-label={`Table ${title}`} className="flex min-w-0 flex-col">
      <ObjectHeader
        dbSchema={table.db_schema}
        name={table.name}
        kind={table.kind}
        status={table.status}
        facts={
          <>
            <dl className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm tabular-nums">
              <Stat label="Rows">
                {table.row_estimate == null
                  ? "unknown"
                  : `~${table.row_estimate.toLocaleString("en-US")}`}
              </Stat>
              <Stat label="Columns">{table.columns.length}</Stat>
              {pks > 0 && (
                <Stat label="PK" badge="pk">
                  {pks}
                </Stat>
              )}
              {fks > 0 && (
                <Stat label="FK" badge="fk">
                  {fks}
                </Stat>
              )}
            </dl>
            {table.comment && (
              <p className="text-sm text-muted-foreground">{table.comment}</p>
            )}
          </>
        }
        actions={
          <ProfileControl
            workspace={workspace}
            systemId={systemId}
            table={table}
            running={running}
            onProfilingStarted={onProfilingStarted}
            onError={setError}
          />
        }
      />
      {error && (
        <div className="px-5 pt-3">
          <ErrorText>{error}</ErrorText>
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab} className="min-w-0">
        <TabsList className="overflow-x-auto px-5">
          <TabsTrigger value="columns" className="text-sm">
            Columns
            <span className="ms-1 font-normal text-subtle-foreground tabular-nums">
              {table.columns.length + removedColumns.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="profile" className="text-sm">
            Profile
          </TabsTrigger>
          <TabsTrigger value="keys" className="text-sm">
            Keys and indexes
            <span className="ms-1 font-normal text-subtle-foreground tabular-nums">
              {keyCount}
            </span>
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="columns"
          className="flex min-w-0 flex-col gap-3 px-5 py-4"
        >
          {table.view_definition && (
            <>
              <h3 className={h5Classes}>View definition</h3>
              <pre className={preClasses}>{table.view_definition}</pre>
            </>
          )}
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-right">#</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Nullable</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>Default</TableHead>
                  <TableHead>Comment</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {table.columns.map((column) => (
                  <TableRow key={column.id}>
                    <TableCell className="text-right text-muted-foreground tabular-nums">
                      {column.ordinal}
                    </TableCell>
                    <TableCell className={`${monoClasses} max-w-64`}>
                      <Truncated text={column.name} />
                      {protectedNames.has(column.name) && (
                        <span className="ml-1.5 inline-flex align-middle">
                          <ProtectedIcon />
                        </span>
                      )}
                      <StatusBadge status={column.status} />
                    </TableCell>
                    <TableCell
                      className={`${monoClasses} text-muted-foreground`}
                    >
                      {column.data_type}
                    </TableCell>
                    <TableCell>{column.is_nullable ? "yes" : "no"}</TableCell>
                    <TableCell>
                      <span className="inline-flex gap-1">
                        {column.is_pk && <Badge variant="pk">PK</Badge>}
                        {isFkColumn(table, column.name) && (
                          <Badge variant="fk">FK</Badge>
                        )}
                      </span>
                    </TableCell>
                    <TableCell className={`${monoClasses} max-w-48`}>
                      {column.default != null && (
                        <Truncated text={String(column.default)} />
                      )}
                    </TableCell>
                    <TableCell className="max-w-64">
                      {column.comment && <Truncated text={column.comment} />}
                    </TableCell>
                  </TableRow>
                ))}
                {removedColumns.map((column) => (
                  <TableRow key={column.id}>
                    <TableCell />
                    <TableCell className={`${monoClasses} max-w-64`}>
                      <Truncated text={column.name} />
                      <StatusBadge status={column.status} />
                    </TableCell>
                    <TableCell
                      className={`${monoClasses} text-muted-foreground`}
                    >
                      {column.data_type}
                    </TableCell>
                    <TableCell />
                    <TableCell />
                    <TableCell />
                    <TableCell />
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
        </TabsContent>
        <TabsContent value="profile" className="min-w-0 px-5 py-4">
          <ProfilePanel
            workspace={workspace}
            systemId={systemId}
            tableId={table.id}
            onProfilingStarted={onProfilingStarted}
            rowEstimate={table.row_estimate}
            keys={Object.fromEntries(
              table.columns.map((c) => [
                c.name,
                { pk: c.is_pk, fk: isFkColumn(table, c.name) },
              ]),
            )}
          />
        </TabsContent>
        <TabsContent
          value="keys"
          className="flex min-w-0 flex-col gap-3 px-5 py-4"
        >
          <h3 className={h5Classes}>
            Keys{" "}
            <span className="font-normal text-subtle-foreground tabular-nums">
              {table.constraints.length}
            </span>
          </h3>
          {table.constraints.length === 0 ? (
            <EmptyState>No keys.</EmptyState>
          ) : (
            <TableCard>
              <Table aria-label="Keys">
                <TableHeader>
                  <TableRow>
                    <TableHead>Type</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Columns</TableHead>
                    <TableHead>References</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {table.constraints.map((key) => (
                    <TableRow key={key.name}>
                      <TableCell>
                        <Badge
                          variant={
                            key.type === "pk"
                              ? "pk"
                              : key.type === "fk"
                                ? "fk"
                                : "neutral"
                          }
                        >
                          {key.type === "pk"
                            ? "PK"
                            : key.type === "fk"
                              ? "FK"
                              : key.type === "unique"
                                ? "Unique"
                                : "Constraint"}
                        </Badge>
                      </TableCell>
                      <TableCell className={monoClasses}>{key.name}</TableCell>
                      <TableCell>
                        <ColumnChips columns={key.columns} />
                      </TableCell>
                      <TableCell className={monoClasses}>
                        {key.type === "fk" && (
                          <span className="inline-flex flex-wrap items-center gap-1.5">
                            <ArrowRight
                              aria-hidden
                              className="size-3.5 text-subtle-foreground"
                            />
                            <span>
                              {key.ref_db_schema}.{key.ref_table}
                            </span>
                            <span className="text-muted-foreground">
                              ({key.ref_columns.join(", ")})
                            </span>
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableCard>
          )}
          <h3 className={h5Classes}>
            Indexes{" "}
            <span className="font-normal text-subtle-foreground tabular-nums">
              {table.indexes.length}
            </span>
          </h3>
          {table.indexes.length === 0 ? (
            <EmptyState>No indexes.</EmptyState>
          ) : (
            <TableCard>
              <Table aria-label="Indexes">
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Columns</TableHead>
                    <TableHead>Unique</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {table.indexes.map((index) => (
                    <TableRow key={index.name}>
                      <TableCell className={monoClasses}>
                        {index.name}
                      </TableCell>
                      <TableCell>
                        <ColumnChips columns={index.columns} />
                      </TableCell>
                      <TableCell>
                        {index.is_unique && <Badge>Unique</Badge>}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableCard>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}

function RoutinePage({ routine }: { routine: Routine }) {
  const title = `${routine.db_schema}.${routine.name}(${routine.signature})`;
  return (
    <section aria-label={`Routine ${title}`} className="flex min-w-0 flex-col">
      <ObjectHeader
        dbSchema={routine.db_schema}
        name={`${routine.name}(${routine.signature})`}
        kind={routine.kind}
        status={routine.status}
      />
      <div className="px-5 py-4">
        {routine.definition ? (
          <pre className={preClasses}>{routine.definition}</pre>
        ) : (
          <p>No code was captured.</p>
        )}
      </div>
    </section>
  );
}
