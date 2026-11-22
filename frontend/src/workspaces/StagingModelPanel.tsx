"use client";

import { Database, Folder, RefreshCw, Search, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  useDwTables,
  useGenerateStaging,
  type DwTableSummary,
  type StagingResult,
} from "../api/dwModel";
import { useSourceSystems, type SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { TreeGroup, TreeLeaf } from "./SchemaBrowser";
import { EmptyState, ErrorText, h3Classes, monoClasses } from "./parts";

type Group = {
  system: SourceSystem;
  schemas: { name: string; tables: DwTableSummary[] }[];
};

/** A Staging Table is named `stg_<system code>_<database schema>_<table>`; the codes are the Workspace's. */
function groupTables(
  tables: DwTableSummary[],
  systems: SourceSystem[],
): { groups: Group[]; other: DwTableSummary[] } {
  // The longest code first, so `erp_eu` wins over `erp` for `stg_erp_eu_dbo_orders`.
  const known = [...systems].sort((a, b) => b.code.length - a.code.length);
  const bySystem = new Map<string, Map<string, DwTableSummary[]>>();
  const other: DwTableSummary[] = [];
  for (const table of tables) {
    const name = table.name.toLowerCase();
    // The longest matching code first; the next one if it leaves no schema and table.
    let placed: { system: SourceSystem; schema: string } | null = null;
    for (const system of known) {
      const prefix = `stg_${system.code.toLowerCase()}_`;
      if (!name.startsWith(prefix)) {
        continue;
      }
      const rest = name.slice(prefix.length);
      const cut = rest.indexOf("_");
      if (cut >= 1 && cut < rest.length - 1) {
        placed = { system, schema: rest.slice(0, cut) };
        break;
      }
    }
    if (!placed) {
      other.push(table);
      continue;
    }
    const { system, schema } = placed;
    const schemas =
      bySystem.get(system.code) ?? new Map<string, DwTableSummary[]>();
    schemas.set(schema, [...(schemas.get(schema) ?? []), table]);
    bySystem.set(system.code, schemas);
  }
  const groups = known
    .filter((s) => bySystem.has(s.code))
    .sort((a, b) => a.code.localeCompare(b.code))
    .map((system) => ({
      system,
      schemas: [...(bySystem.get(system.code) ?? [])]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([name, items]) => ({ name, tables: items })),
    }));
  return { groups, other };
}

/** The "Generated" strip after a run: what was created, and what to review. */
function ResultStrip({ result }: { result: StagingResult }) {
  return (
    <div
      role="status"
      aria-label="Generation result"
      className="flex flex-col gap-3 rounded-pill border border-border bg-surface-raised p-4 text-sm"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="success">Generated</Badge>
        <span>
          {result.tables_created}{" "}
          {result.tables_created === 1 ? "Staging Table" : "Staging Tables"} and{" "}
          {result.columns_created}{" "}
          {result.columns_created === 1 ? "column" : "columns"} created.{" "}
          {result.tables_existing}{" "}
          {result.tables_existing === 1 ? "source table" : "source tables"}{" "}
          already had one and were left as they are.
        </span>
        {result.flags.length > 0 && (
          <Badge variant="warning">{result.flags.length} to review</Badge>
        )}
      </div>
      {result.flags.length > 0 && (
        <ul aria-label="To review" className="flex flex-col gap-1.5">
          {result.flags.map((flag, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span className={monoClasses}>
                {flag.column_name
                  ? `${flag.table_name}.${flag.column_name}`
                  : flag.table_name}
              </span>
              <Badge variant="warning" className="font-mono">
                {flag.code}
              </Badge>
              <span className="text-muted-foreground">{flag.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The Staging Layer's Model folder: laid out like the Source schema (a summary header, a tree
 * of the Staging Tables by System Code and Database Schema, the open table beside it), with
 * the button that generates the Layer for owners and editors. `renderDetails` draws the open
 * table, so this panel does not have to know how.
 */
export function StagingModelPanel({
  workspace,
  renderDetails,
}: {
  workspace: Workspace;
  renderDetails: (tableId: string, onClose: () => void) => ReactNode;
}) {
  const tables = useDwTables(workspace.id, "staging");
  const systems = useSourceSystems(workspace.id);
  const generate = useGenerateStaging(workspace.id);
  const [result, setResult] = useState<StagingResult | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const canGenerate = allows(workspace, "dw_schema.edit");

  if (tables.isPending) {
    return <Loading />;
  }
  if (tables.isError && !tables.data) {
    return (
      <ErrorText>Could not load the model: {tables.error.message}</ErrorText>
    );
  }
  const tableData = tables.data ?? [];
  // Without the Source Systems every table is listed under "Other".
  const systemList = systems.data ?? [];

  const button = (
    <Button
      type="button"
      variant="primary"
      disabled={generate.isPending}
      onClick={() => {
        setResult(null);
        generate.reset();
        generate.mutate(undefined, { onSuccess: (data) => setResult(data) });
      }}
    >
      <Database aria-hidden />
      Generate Staging Layer
    </Button>
  );

  const query = search.trim().toLowerCase();
  const shown = tableData.filter((t) => t.name.toLowerCase().includes(query));
  const { groups, other } = groupTables(shown, systemList);
  const systemCount = groupTables(tableData, systemList).groups.length;
  const columns = tableData.reduce((sum, t) => sum + t.column_count, 0);
  const isOpen = (key: string) => query !== "" || !closed.has(key);
  const toggle = (key: string) =>
    setClosed((current) => {
      const next = new Set(current);
      if (!next.delete(key)) {
        next.add(key);
      }
      return next;
    });

  const leaf = (table: DwTableSummary, level: number) => (
    <TreeLeaf
      key={table.id}
      name={table.name}
      title={table.name}
      icon={<Table2 />}
      level={level}
      selected={table.id === openId}
      onOpen={() => setOpenId(table.id)}
      after={
        <>
          {table.naming_violation_count > 0 && (
            <span
              role="img"
              aria-label="Naming violations"
              title="Naming violations"
              className="size-1.5 shrink-0 rounded-full bg-warning"
            />
          )}
          <span className="text-xs text-subtle-foreground tabular-nums">
            {table.column_count}
          </span>
        </>
      }
    />
  );

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-5">
      <header className="flex flex-col gap-4 border-b border-border pb-5 xl:flex-row xl:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h3 id="folder-title" className={h3Classes}>
            Staging model
          </h3>
          <p className="text-sm text-muted-foreground tabular-nums">
            {tableData.length} {tableData.length === 1 ? "table" : "tables"} ·{" "}
            {columns} {columns === 1 ? "column" : "columns"} · {systemCount}{" "}
            {systemCount === 1 ? "Source System" : "Source Systems"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh"
            title="Refresh the Staging Tables"
            onClick={() => void tables.refetch()}
          >
            <RefreshCw
              aria-hidden
              className={cn(tables.isFetching && "motion-safe:animate-spin")}
            />
          </Button>
          {canGenerate && tableData.length > 0 && button}
        </div>
      </header>
      {tables.isError && (
        <ErrorText>
          Could not refresh the model: {tables.error.message}
        </ErrorText>
      )}
      {generate.isError && <ErrorText>{generate.error.message}</ErrorText>}
      {result && <ResultStrip result={result} />}
      {tableData.length === 0 ? (
        <EmptyState title="No Staging Tables yet">
          Staging Tables are created from the Source Schema: one per source
          table, named stg_&lt;system code&gt;_&lt;schema&gt;_&lt;table&gt;,
          with translated types and audit columns. Running it again adds only
          what is new.
          {canGenerate && <span className="mt-3 block">{button}</span>}
        </EmptyState>
      ) : (
        <div className="grid overflow-hidden rounded-pill border border-border lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col border-b border-border bg-surface lg:border-e lg:border-b-0">
            <div className="border-b border-border p-3">
              <label htmlFor="staging-search" className="sr-only">
                Search by name
              </label>
              <div className="relative">
                <Search
                  aria-hidden
                  className="pointer-events-none absolute start-2.5 top-2 size-4 text-subtle-foreground"
                />
                <Input
                  id="staging-search"
                  type="search"
                  value={search}
                  placeholder="A Staging Table"
                  onChange={(event) => setSearch(event.target.value)}
                  className="ps-8"
                />
              </div>
            </div>
            <ul
              aria-label="Source Systems"
              className="flex flex-1 flex-col gap-px overflow-auto p-2"
            >
              {groups.map(({ system, schemas }) => (
                <TreeGroup
                  key={system.code}
                  label={
                    <>
                      {system.code}{" "}
                      <span className="ms-2 font-sans text-xs font-normal text-muted-foreground">
                        {system.name}
                      </span>
                    </>
                  }
                  icon={<Folder />}
                  count={schemas.reduce((n, s) => n + s.tables.length, 0)}
                  level={0}
                  strong
                  open={isOpen(system.code)}
                  onToggle={() => toggle(system.code)}
                >
                  {schemas.map((schema) => (
                    <TreeGroup
                      key={schema.name}
                      label={schema.name}
                      icon={<Folder />}
                      count={schema.tables.length}
                      level={1}
                      listLabel={`Tables of ${system.code} ${schema.name}`}
                      open={isOpen(`${system.code}/${schema.name}`)}
                      onToggle={() => toggle(`${system.code}/${schema.name}`)}
                    >
                      {schema.tables.map((t) => leaf(t, 2))}
                    </TreeGroup>
                  ))}
                </TreeGroup>
              ))}
              {other.length > 0 && (
                <TreeGroup
                  label="Other"
                  icon={<Folder />}
                  count={other.length}
                  level={0}
                  strong
                  open={isOpen("other")}
                  onToggle={() => toggle("other")}
                >
                  {other.map((t) => leaf(t, 1))}
                </TreeGroup>
              )}
              {shown.length === 0 && (
                <li className="px-2 py-1 text-xs text-subtle-foreground">
                  No Staging Table matches.
                </li>
              )}
            </ul>
          </div>
          <div className="min-w-0 p-4">
            {openId ? (
              renderDetails(openId, () => setOpenId(null))
            ) : (
              <EmptyState>Open a Staging Table from the tree.</EmptyState>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
