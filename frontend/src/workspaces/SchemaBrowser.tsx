"use client";

import { useEffect, useState } from "react";

import {
  useSchemaSearch,
  useSourceSchema,
  type SchemaSearchHit,
  type SourceSchema,
} from "../api/snapshots";
import type { Workspace } from "../api/workspaces";
import { ProfilePanel } from "./ProfilePanel";

type Table = SourceSchema["tables"][number];
type Routine = SourceSchema["routines"][number];
type RemovedColumn = SourceSchema["removed_columns"][number];
type Selection = { kind: "table"; id: string } | { kind: "routine"; id: string } | null;

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
  return <span className="status-badge"> ({STATUS_LABELS[status] ?? status})</span>;
}

function useDebounced(value: string, ms: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

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
    return <p>Loading the Source Schema…</p>;
  }
  if (schema.isError) {
    return <p role="alert">Could not load the Source Schema: {schema.error.message}</p>;
  }
  const content = schema.data;

  function open(hit: SchemaSearchHit) {
    if (hit.kind === "routine") {
      setSelection({ kind: "routine", id: hit.id });
      return;
    }
    const table =
      hit.kind === "column"
        ? content.tables.find((t) => t.db_schema === hit.db_schema && t.name === hit.table)
        : content.tables.find((t) => t.id === hit.id);
    if (table) {
      setSelection({ kind: "table", id: table.id });
    }
  }

  const table =
    selection?.kind === "table" ? content.tables.find((t) => t.id === selection.id) : undefined;
  const routine =
    selection?.kind === "routine" ? content.routines.find((r) => r.id === selection.id) : undefined;

  return (
    <div className="schema-browser">
      <div className="form">
        <label htmlFor="schema-search">Search by name</label>
        <input
          id="schema-search"
          type="search"
          value={search}
          placeholder="A table, column, routine or Database Schema"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {query !== "" && <SearchResults query={query} results={results} onOpen={open} />}
      <ul aria-label="Database Schemas">
        {content.db_schemas.map((dbSchema) => (
          <li key={dbSchema.id}>
            <strong>{dbSchema.name}</strong>
            <StatusBadge status={dbSchema.status} />
            <ul aria-label={`Tables and views of ${dbSchema.name}`}>
              {content.tables
                .filter((t) => t.db_schema === dbSchema.name)
                .map((t) => (
                  <li key={t.id}>
                    <button
                      type="button"
                      className="link-button"
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
                  <li key={t.id}>
                    {t.name}
                    {t.kind === "view" && " (view)"}
                    <StatusBadge status={t.status} />
                  </li>
                ))}
            </ul>
            <ul aria-label={`Routines of ${dbSchema.name}`}>
              {content.routines
                .filter((r) => r.db_schema === dbSchema.name)
                .map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => setSelection({ kind: "routine", id: r.id })}
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
          removedColumns={content.removed_columns.filter((c) => c.table_id === table.id)}
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
    return <p>Searching…</p>;
  }
  if (results.isError) {
    return <p role="alert">Could not search: {results.error.message}</p>;
  }
  if (results.data.length === 0) {
    return <p className="empty-state">Nothing is named like “{query}”.</p>;
  }
  return (
    <ul aria-label="Search results">
      {results.data.map((hit) => (
        <li key={`${hit.kind}-${hit.id}`}>
          <button type="button" className="link-button" onClick={() => onOpen(hit)}>
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
  table: Table;
  workspace: Workspace;
  systemId: string;
  removedColumns: RemovedColumn[];
}) {
  const title = `${table.db_schema}.${table.name}`;
  return (
    <section aria-label={`Table ${title}`}>
      <h4>
        {title} <small>{table.kind}</small>
        <StatusBadge status={table.status} />
      </h4>
      <dl className="details">
        <dt>Row estimate</dt>
        <dd>{table.row_estimate ?? "unknown"}</dd>
        {table.comment && (
          <>
            <dt>Comment</dt>
            <dd>{table.comment}</dd>
          </>
        )}
      </dl>
      {table.view_definition && (
        <>
          <h5>View definition</h5>
          <pre>{table.view_definition}</pre>
        </>
      )}
      <h5>Columns</h5>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Name</th>
            <th>Type</th>
            <th>Nullable</th>
            <th>Key</th>
            <th>Default</th>
            <th>Comment</th>
          </tr>
        </thead>
        <tbody>
          {table.columns.map((column) => (
            <tr key={column.id}>
              <td>{column.ordinal}</td>
              <td>
                {column.name}
                <StatusBadge status={column.status} />
              </td>
              <td>{column.data_type}</td>
              <td>{column.is_nullable ? "yes" : "no"}</td>
              <td>{column.is_pk ? "PK" : ""}</td>
              <td>{column.default}</td>
              <td>{column.comment}</td>
            </tr>
          ))}
          {removedColumns.map((column) => (
            <tr key={column.id}>
              <td />
              <td>
                {column.name}
                <StatusBadge status={column.status} />
              </td>
              <td>{column.data_type}</td>
              <td />
              <td />
              <td />
              <td />
            </tr>
          ))}
        </tbody>
      </table>
      <ProfilePanel workspace={workspace} systemId={systemId} tableId={table.id} />
      <h5>Keys</h5>
      {table.constraints.length === 0 ? (
        <p className="empty-state">No keys.</p>
      ) : (
        <ul aria-label="Keys">
          {table.constraints.map((key) => (
            <li key={key.name}>
              {key.name}: {key.type.toUpperCase()} ({key.columns.join(", ")})
              {key.type === "fk" &&
                ` references ${key.ref_db_schema}.${key.ref_table} (${key.ref_columns.join(", ")})`}
            </li>
          ))}
        </ul>
      )}
      <h5>Indexes</h5>
      {table.indexes.length === 0 ? (
        <p className="empty-state">No indexes.</p>
      ) : (
        <ul aria-label="Indexes">
          {table.indexes.map((index) => (
            <li key={index.name}>
              {index.name} ({index.columns.join(", ")}){index.is_unique && " unique"}
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
    <section aria-label={`Routine ${title}`}>
      <h4>
        {title} <small>{routine.kind}</small>
        <StatusBadge status={routine.status} />
      </h4>
      {routine.definition ? <pre>{routine.definition}</pre> : <p>No code was captured.</p>}
    </section>
  );
}
