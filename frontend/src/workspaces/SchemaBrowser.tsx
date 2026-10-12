"use client";

import { useEffect, useState } from "react";

import {
  useSchemaSearch,
  useSourceSchema,
  type SchemaSearchHit,
  type SourceSchema,
} from "../api/snapshots";
import type { Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { ProfilePanel } from "./ProfilePanel";
import {
  DetailList,
  EmptyState,
  ErrorText,
  TableCard,
  h4Classes,
  h5Classes,
  labelClasses,
  linkButtonClasses,
  listCardClasses,
  monoClasses,
  preClasses,
} from "./parts";

type SchemaTable = SourceSchema["tables"][number];
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
    <Badge variant="warning" className="ml-2">
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
const rowClasses = "px-3 py-1.5";

/**
 * Browses a Source System's Source Schema (spec story 54): Database Schemas with their
 * tables, views and routines, a page per table or routine, and a search by name. Any
 * member can browse. Objects that are not `present` carry their state.
 */
export function SchemaBrowser({
  workspace,
  systemId,
}: {
  workspace: Workspace;
  systemId: string;
}) {
  const workspaceId = workspace.id;
  const schema = useSourceSchema(workspaceId, systemId, true);
  const [selection, setSelection] = useState<Selection>(null);
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim(), 250);
  const results = useSchemaSearch(workspaceId, systemId, query);

  if (schema.isPending) {
    return <p className="text-sand-11">Loading the Source Schema…</p>;
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

  const table =
    selection?.kind === "table"
      ? content.tables.find((t) => t.id === selection.id)
      : undefined;
  const routine =
    selection?.kind === "routine"
      ? content.routines.find((r) => r.id === selection.id)
      : undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className={`${labelClasses} max-w-xl`}>
        <label htmlFor="schema-search">Search by name</label>
        <Input
          id="schema-search"
          type="search"
          value={search}
          placeholder="A table, column, routine or Database Schema"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {query !== "" && (
        <SearchResults query={query} results={results} onOpen={open} />
      )}
      <ul aria-label="Database Schemas" className="flex flex-col gap-3">
        {content.db_schemas.map((dbSchema) => (
          <li key={dbSchema.id} className="flex flex-col gap-1.5">
            <div>
              <strong className={`${monoClasses} font-semibold`}>
                {dbSchema.name}
              </strong>
              <StatusBadge status={dbSchema.status} />
            </div>
            <ul
              aria-label={`Tables and views of ${dbSchema.name}`}
              className={listCardClasses}
            >
              {content.tables
                .filter((t) => t.db_schema === dbSchema.name)
                .map((t) => (
                  <li key={t.id} className={rowClasses}>
                    <button
                      type="button"
                      className={linkButtonClasses}
                      onClick={() => setSelection({ kind: "table", id: t.id })}
                    >
                      {t.name}
                    </button>
                    {t.kind === "view" && " (view)"}
                    <StatusBadge status={t.status} />
                  </li>
                ))}
              {content.removed_tables
                .filter((t) => t.db_schema === dbSchema.name)
                .map((t) => (
                  <li key={t.id} className={`${rowClasses} text-sand-11`}>
                    <span className={monoClasses}>{t.name}</span>
                    {t.kind === "view" && " (view)"}
                    <StatusBadge status={t.status} />
                  </li>
                ))}
            </ul>
            <ul
              aria-label={`Routines of ${dbSchema.name}`}
              className={listCardClasses}
            >
              {content.routines
                .filter((r) => r.db_schema === dbSchema.name)
                .map((r) => (
                  <li key={r.id} className={rowClasses}>
                    <button
                      type="button"
                      className={linkButtonClasses}
                      onClick={() =>
                        setSelection({ kind: "routine", id: r.id })
                      }
                    >
                      {r.name}({r.signature})
                    </button>
                    {` (${r.kind})`}
                    <StatusBadge status={r.status} />
                  </li>
                ))}
            </ul>
          </li>
        ))}
      </ul>
      {table && (
        <TablePage
          table={table}
          workspace={workspace}
          systemId={systemId}
          removedColumns={content.removed_columns.filter(
            (c) => c.table_id === table.id,
          )}
        />
      )}
      {routine && <RoutinePage routine={routine} />}
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
    return <p className="text-sand-11">Searching…</p>;
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
            className={linkButtonClasses}
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

function TablePage({
  table,
  workspace,
  systemId,
  removedColumns,
}: {
  table: SchemaTable;
  workspace: Workspace;
  systemId: string;
  removedColumns: RemovedColumn[];
}) {
  const title = `${table.db_schema}.${table.name}`;
  return (
    <section
      aria-label={`Table ${title}`}
      className="flex flex-col gap-3 rounded-xl border border-sand-5 bg-sand-1 p-5"
    >
      <h4
        className={`${h4Classes} ${monoClasses} flex flex-wrap items-center gap-2`}
      >
        {title} <Badge>{table.kind}</Badge>
        <StatusBadge status={table.status} />
      </h4>
      <DetailList>
        <dt>Row estimate</dt>
        <dd>{table.row_estimate ?? "unknown"}</dd>
        {table.comment && (
          <>
            <dt>Comment</dt>
            <dd>{table.comment}</dd>
          </>
        )}
      </DetailList>
      {table.view_definition && (
        <>
          <h5 className={h5Classes}>View definition</h5>
          <pre className={preClasses}>{table.view_definition}</pre>
        </>
      )}
      <h5 className={h5Classes}>Columns</h5>
      <TableCard>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
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
                <TableCell className="text-sand-11">{column.ordinal}</TableCell>
                <TableCell className={monoClasses}>
                  {column.name}
                  <StatusBadge status={column.status} />
                </TableCell>
                <TableCell className={`${monoClasses} text-sand-11`}>
                  {column.data_type}
                </TableCell>
                <TableCell>{column.is_nullable ? "yes" : "no"}</TableCell>
                <TableCell>{column.is_pk ? "PK" : ""}</TableCell>
                <TableCell className={monoClasses}>{column.default}</TableCell>
                <TableCell>{column.comment}</TableCell>
              </TableRow>
            ))}
            {removedColumns.map((column) => (
              <TableRow key={column.id}>
                <TableCell />
                <TableCell className={monoClasses}>
                  {column.name}
                  <StatusBadge status={column.status} />
                </TableCell>
                <TableCell className={`${monoClasses} text-sand-11`}>
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
      <ProfilePanel
        workspace={workspace}
        systemId={systemId}
        tableId={table.id}
      />
      <h5 className={h5Classes}>Keys</h5>
      {table.constraints.length === 0 ? (
        <EmptyState>No keys.</EmptyState>
      ) : (
        <ul aria-label="Keys" className={listCardClasses}>
          {table.constraints.map((key) => (
            <li key={key.name} className={`${rowClasses} ${monoClasses}`}>
              {key.name}: {key.type.toUpperCase()} ({key.columns.join(", ")})
              {key.type === "fk" &&
                ` references ${key.ref_db_schema}.${key.ref_table} (${key.ref_columns.join(", ")})`}
            </li>
          ))}
        </ul>
      )}
      <h5 className={h5Classes}>Indexes</h5>
      {table.indexes.length === 0 ? (
        <EmptyState>No indexes.</EmptyState>
      ) : (
        <ul aria-label="Indexes" className={listCardClasses}>
          {table.indexes.map((index) => (
            <li key={index.name} className={`${rowClasses} ${monoClasses}`}>
              {index.name} ({index.columns.join(", ")})
              {index.is_unique && " unique"}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function RoutinePage({ routine }: { routine: Routine }) {
  const title = `${routine.db_schema}.${routine.name}(${routine.signature})`;
  return (
    <section
      aria-label={`Routine ${title}`}
      className="flex flex-col gap-3 rounded-xl border border-sand-5 bg-sand-1 p-5"
    >
      <h4
        className={`${h4Classes} ${monoClasses} flex flex-wrap items-center gap-2`}
      >
        {title} <Badge>{routine.kind}</Badge>
        <StatusBadge status={routine.status} />
      </h4>
      {routine.definition ? (
        <pre className={preClasses}>{routine.definition}</pre>
      ) : (
        <p>No code was captured.</p>
      )}
    </section>
  );
}
