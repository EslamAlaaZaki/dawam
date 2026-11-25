"use client";

import { ArrowRight, ChevronRight, Database, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import {
  stagingMappingSheetUrl,
  useStagingMappings,
  type StagingMappingColumn,
  type StagingMappingTable,
} from "../api/mappings";
import type { Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { typeText } from "../lib/typeText";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import {
  ExportSheetMenu,
  LayerStrip,
  MappingsExplorer,
  StateDot,
} from "./mappingParts";
import { EmptyState, ErrorText, h3Classes, monoClasses } from "./parts";
import { TreeGroup, TreeLeaf } from "./SchemaBrowser";

const FLAG_LABELS: Record<string, string> = {
  placeholder: "Placeholder name",
  truncated: "Truncated",
  collision: "Collision",
  lossy_type: "Lossy type",
  fallback_type: "Fallback type",
};

type Filter = "all" | "hashed" | "review" | "audit";

const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["hashed", "Hashed"],
  ["review", "To review"],
  ["audit", "Audit"],
];

const DOTS = {
  review: { label: "To review", className: "bg-warning" },
  hashed: { label: "Has hashed", className: "bg-danger" },
  direct: { label: "Copied direct", className: "bg-success" },
};

const reviewLabels = (column: StagingMappingColumn): string[] => [
  ...column.review_flags.map((f) => FLAG_LABELS[f.code] ?? f.code),
  ...(column.status === "source_removed" ? ["Source removed"] : []),
];

const matches = (column: StagingMappingColumn, filter: Filter) =>
  filter === "all" ||
  (filter === "hashed" && column.mapping_type === "hashed") ||
  (filter === "audit" && column.mapping_type === "system") ||
  (filter === "review" && reviewLabels(column).length > 0);

/** The state a table's dot shows: to review wins over hashed, which wins over direct. */
function tableState(table: StagingMappingTable) {
  if (table.columns.some((c) => reviewLabels(c).length > 0)) {
    return DOTS.review;
  }
  if (table.columns.some((c) => c.mapping_type === "hashed")) {
    return DOTS.hashed;
  }
  return DOTS.direct;
}

/**
 * The Staging Layer's Mappings page: how each Staging column is copied from its source
 * column. DAWAM generates them; hashing changes only on the PII page, which `onOpenPii`
 * opens for a Source System. `onOpenModel` opens the Model page.
 */
export function StagingMappingsPanel({
  workspace,
  onOpenPii,
  onOpenModel,
}: {
  workspace: Workspace;
  onOpenPii: (systemId: string) => void;
  onOpenModel: () => void;
}) {
  const mappings = useStagingMappings(workspace.id);
  const [filter, setFilter] = useState<Filter>("all");
  const [text, setText] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [closed, setClosed] = useState<Set<string>>(new Set());

  const head = (full: boolean, extra?: ReactNode) => (
    <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <h3
          id="folder-title"
          className={cn(
            h3Classes,
            "flex flex-wrap items-center gap-x-2 gap-y-1",
          )}
        >
          Staging mappings
          {full && (
            <Badge variant="success" dot>
              Fully mapped
            </Badge>
          )}
        </h3>
        <p className="text-sm text-muted-foreground">
          Each staging column is copied 1:1 from its source column. DAWAM
          generates these, so there is nothing to map by hand. Review the
          columns that are hashed or had to change on the way.
        </p>
        {extra}
      </div>
      <LayerStrip active={["Source", "Staging"]} />
    </header>
  );

  if (mappings.isPending) {
    return <Loading />;
  }
  if (mappings.isError) {
    return (
      <section aria-labelledby="folder-title" className="flex flex-col gap-5">
        {head(false)}
        <ErrorText>
          Could not load the Staging mappings: {mappings.error.message}
        </ErrorText>
      </section>
    );
  }
  const { tables, totals } = mappings.data;

  if (tables.length === 0) {
    return (
      <section aria-labelledby="folder-title" className="flex flex-col gap-5">
        {head(false)}
        <EmptyState title="No staging mappings yet">
          Generate the Staging Layer on the Model page. Its mappings show here
          once it exists.
          <span className="mt-3 block">
            <Button type="button" variant="secondary" onClick={onOpenModel}>
              Open Model
              <ArrowRight aria-hidden />
            </Button>
          </span>
        </EmptyState>
      </section>
    );
  }

  const exportMenu = (
    <ExportSheetMenu
      url={(format) => stagingMappingSheetUrl(workspace.id, format)}
    />
  );

  const all = tables.flatMap((t) => t.columns);
  const counts: Record<Filter, number> = {
    all: all.length,
    hashed: all.filter((c) => matches(c, "hashed")).length,
    review: all.filter((c) => matches(c, "review")).length,
    audit: all.filter((c) => matches(c, "audit")).length,
  };
  const needle = text.trim().toLowerCase();
  const filtering = filter !== "all" || needle !== "";
  const visible = tables
    .map((table) => {
      const tableHit = table.table_name.toLowerCase().includes(needle);
      const columns = table.columns.filter(
        (c) =>
          matches(c, filter) &&
          (tableHit ||
            c.column_name.toLowerCase().includes(needle) ||
            (c.source_column_name ?? "").toLowerCase().includes(needle)),
      );
      return { table, columns };
    })
    .filter(({ columns }) => !filtering || columns.length > 0);
  // The clicked table, or the first visible one when it is filtered out.
  const selected =
    visible.find(({ table }) => table.table_id === chosen) ??
    visible[0] ??
    null;
  const groups: [string, typeof visible][] = [];
  for (const entry of visible) {
    const name = entry.table.system_name ?? "No Source System";
    const group = groups.find(([key]) => key === name);
    if (group) {
      group[1].push(entry);
    } else {
      groups.push([name, [entry]]);
    }
  }

  const share = (n: number) =>
    totals.columns > 0 ? `${(n / totals.columns) * 100}%` : "0%";

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      {head(true, exportMenu)}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Coverage">
          <Big>100%</Big>
          <span className="text-sm text-muted-foreground tabular-nums">
            {totals.columns} / {totals.columns} columns
          </span>
          <span
            aria-hidden
            className="flex h-1.5 overflow-hidden rounded-full bg-muted"
          >
            <span
              className="bg-success"
              style={{ width: share(totals.direct) }}
            />
            <span
              className="bg-danger"
              style={{ width: share(totals.hashed) }}
            />
            <span
              className="bg-subtle-foreground"
              style={{ width: share(totals.audit) }}
            />
          </span>
        </Stat>
        <Stat label="Copied direct">
          <Big>{totals.direct}</Big>
          <Note>
            across {totals.tables}{" "}
            {totals.tables === 1 ? "Staging Table" : "Staging Tables"}
          </Note>
        </Stat>
        <Stat label="Hashed (PII)">
          <Big className="text-danger-strong">{totals.hashed}</Big>
          <Note>salted SHA-256 of the source</Note>
        </Stat>
        <Stat label="To review">
          <Big className="text-warning-strong">{totals.to_review}</Big>
          <Note>renamed, retyped or removed</Note>
        </Stat>
      </div>

      <section aria-labelledby="mappings-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h4 id="mappings-title" className="text-xl font-semibold">
            Column mappings
          </h4>
          <div
            role="group"
            aria-label="Show"
            className="flex gap-0.5 rounded-lg bg-muted p-0.5"
          >
            {FILTERS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={filter === id}
                onClick={() => setFilter(id)}
                className={cn(
                  "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-md px-2.5 text-sm font-medium transition-colors",
                  filter === id
                    ? "bg-surface-raised text-foreground ring-1 ring-border"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
                <span className="text-subtle-foreground tabular-nums">
                  {counts[id]}
                </span>
              </button>
            ))}
          </div>
        </div>
        <MappingsExplorer
          idPrefix="staging-mappings"
          placeholder="A table or column"
          treeLabel="Staging tables by Source System"
          search={text}
          onSearch={setText}
          empty={visible.length === 0}
          legend={[DOTS.direct, DOTS.hashed, DOTS.review]}
          tree={groups.map(([name, entries]) => (
            <TreeGroup
              key={name}
              label={name}
              icon={<Database />}
              count={entries.length}
              level={0}
              open={filtering || !closed.has(name)}
              onToggle={() =>
                setClosed((c) => {
                  const next = new Set(c);
                  if (!next.delete(name)) {
                    next.add(name);
                  }
                  return next;
                })
              }
            >
              {entries.map(({ table, columns }) => {
                const state = tableState(table);
                return (
                  <TreeLeaf
                    key={table.table_id}
                    name={table.table_name}
                    title={table.table_name}
                    icon={<Table2 />}
                    level={1}
                    selected={table.table_id === selected?.table.table_id}
                    onOpen={() => setChosen(table.table_id)}
                    after={
                      <>
                        {filter !== "all" && (
                          <span className="text-xs text-subtle-foreground tabular-nums">
                            {columns.length}
                          </span>
                        )}
                        <StateDot
                          label={state.label}
                          className={state.className}
                        />
                      </>
                    }
                  />
                );
              })}
            </TreeGroup>
          ))}
        >
          {selected ? (
            <TableDetail
              table={selected.table}
              columns={selected.columns}
              onOpenPii={onOpenPii}
            />
          ) : (
            <EmptyState
              className="m-5"
              title={
                filter === "all"
                  ? "No table or column matches."
                  : "No column matches this filter."
              }
            >
              Try another filter or search.
            </EmptyState>
          )}
        </MappingsExplorer>
      </section>
    </section>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-raised p-4"
    >
      <span className="text-sm font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Big({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("text-3xl font-semibold tabular-nums", className)}>
      {children}
    </span>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <span className="text-sm text-subtle-foreground">{children}</span>;
}

/** The column grid: source column, arrow, staging column, mapping, review. */
const gridCols =
  "grid grid-cols-[minmax(0,1.25fr)_1.75rem_minmax(0,1.25fr)_6rem_minmax(0,1fr)] gap-3";

const ARROW: Record<StagingMappingColumn["mapping_type"], string> = {
  direct: "text-success",
  hashed: "text-danger",
  system: "text-border-strong",
};

/** The chosen table: its header, then the columns that pass the filter and search. */
function TableDetail({
  table,
  columns,
  onOpenPii,
}: {
  table: StagingMappingTable;
  columns: StagingMappingColumn[];
  onOpenPii: (systemId: string) => void;
}) {
  const hashed = table.columns.filter(
    (c) => c.mapping_type === "hashed",
  ).length;
  const review = table.columns.filter((c) => reviewLabels(c).length > 0).length;
  const source = table.source_table_name
    ? [table.source_db_schema, table.source_table_name]
        .filter(Boolean)
        .join(".")
    : null;
  const systemId = table.system_id;
  return (
    <section
      aria-label={`Mappings of ${table.table_name}`}
      className="flex min-w-0 flex-col"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-5 py-4">
        <h5 className={cn(monoClasses, "text-xl font-medium")}>
          {table.table_name}
        </h5>
        {table.system_name && <Badge>{table.system_name}</Badge>}
        {hashed > 0 && <Badge variant="danger">{hashed} hashed</Badge>}
        {review > 0 && <Badge variant="warning">{review} to review</Badge>}
        <span className="ms-auto text-sm text-muted-foreground tabular-nums">
          {source && (
            <>
              from <span className={monoClasses}>{source}</span> ·{" "}
            </>
          )}
          {table.columns.length} columns
        </span>
      </div>
      <div className="overflow-x-auto">
        <div
          role="table"
          aria-label={`Columns of ${table.table_name}`}
          className="min-w-180"
        >
          <div
            role="row"
            className={cn(
              gridCols,
              "border-b border-border px-5 py-2.5 text-xs font-medium text-muted-foreground",
            )}
          >
            <span role="columnheader">Source column</span>
            <span role="columnheader">
              <span className="sr-only">Copied to</span>
            </span>
            <span role="columnheader">Staging column</span>
            <span role="columnheader">Mapping</span>
            <span role="columnheader">Review</span>
          </div>
          {columns.map((column) => (
            <div
              role="row"
              key={column.column_id}
              className={cn(
                gridCols,
                "min-h-10 items-center border-b border-muted px-5 py-1 text-sm last:border-b-0",
              )}
            >
              <span
                role="cell"
                className="flex min-w-0 flex-wrap items-baseline gap-x-2"
              >
                {column.source_column_name ? (
                  <>
                    <span className={cn(monoClasses, "break-all")}>
                      {column.source_column_name}
                    </span>
                    <span
                      className={cn(
                        monoClasses,
                        "text-xs text-muted-foreground",
                      )}
                    >
                      {column.source_data_type}
                    </span>
                  </>
                ) : (
                  <span
                    className={cn(monoClasses, "text-subtle-foreground italic")}
                  >
                    none
                  </span>
                )}
              </span>
              <span role="cell" className="flex">
                <ArrowRight
                  aria-hidden
                  className={cn("size-4", ARROW[column.mapping_type])}
                />
              </span>
              <span
                role="cell"
                className="flex min-w-0 flex-wrap items-baseline gap-x-2"
              >
                <span className={cn(monoClasses, "font-medium break-all")}>
                  {column.column_name}
                </span>
                <span
                  className={cn(monoClasses, "text-xs text-muted-foreground")}
                >
                  {typeText(column.data_type)}
                </span>
              </span>
              <span role="cell" className="flex">
                {column.mapping_type === "direct" && <Badge>Direct</Badge>}
                {column.mapping_type === "hashed" && (
                  <Badge variant="danger">Hashed</Badge>
                )}
                {column.mapping_type === "system" && (
                  <Badge className="bg-transparent text-muted-foreground ring-1 ring-border-strong ring-inset">
                    Audit
                  </Badge>
                )}
              </span>
              <span role="cell" className="flex flex-wrap items-center gap-2">
                {reviewLabels(column).map((label) => (
                  <Badge key={label} variant="warning">
                    {label}
                  </Badge>
                ))}
                {column.mapping_type === "hashed" && systemId && (
                  <a
                    href={`?${new URLSearchParams({ folder: `systems/${systemId}/pii` })}`}
                    className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                    onClick={(event) => {
                      event.preventDefault();
                      onOpenPii(systemId);
                    }}
                  >
                    Change in PII
                    <ChevronRight aria-hidden className="size-3" />
                  </a>
                )}
                {column.mapping_type === "system" && (
                  <span className="text-xs text-muted-foreground">
                    Set at load
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
