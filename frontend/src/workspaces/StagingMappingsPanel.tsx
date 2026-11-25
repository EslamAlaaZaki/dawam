"use client";

import { ArrowRight, ChevronDown } from "lucide-react";
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
import { Input } from "../components/ui/input";
import { typeText } from "../lib/typeText";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { ExportSheetMenu, LayerStrip } from "./mappingParts";
import { EmptyState, ErrorText, h3Classes, monoClasses } from "./parts";

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

/** The first groups start open; the rest start closed. */
const OPEN_AT_FIRST = 3;

const reviewLabels = (column: StagingMappingColumn): string[] => [
  ...column.review_flags.map((f) => FLAG_LABELS[f.code] ?? f.code),
  ...(column.status === "source_removed" ? ["Source removed"] : []),
];

const matches = (column: StagingMappingColumn, filter: Filter) =>
  filter === "all" ||
  (filter === "hashed" && column.mapping_type === "hashed") ||
  (filter === "audit" && column.mapping_type === "system") ||
  (filter === "review" && reviewLabels(column).length > 0);

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
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  const head = (full: boolean, extra?: ReactNode) => (
    <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <h3
          id="folder-title"
          className={cn(h3Classes, "flex flex-wrap items-center gap-x-2 gap-y-1")}
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
    <ExportSheetMenu url={(format) => stagingMappingSheetUrl(workspace.id, format)} />
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
          (tableHit || c.column_name.toLowerCase().includes(needle)),
      );
      return { table, columns };
    })
    .filter(({ columns }) => !filtering || columns.length > 0);

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
            <span className="bg-success" style={{ width: share(totals.direct) }} />
            <span className="bg-danger" style={{ width: share(totals.hashed) }} />
            <span
              className="bg-subtle-foreground"
              style={{ width: share(totals.audit) }}
            />
          </span>
        </Stat>
        <Stat label="Copied direct">
          <Big>{totals.direct}</Big>
        </Stat>
        <Stat label="Hashed (PII)">
          <Big>{totals.hashed}</Big>
        </Stat>
        <Stat label="To review">
          <Big>{totals.to_review}</Big>
        </Stat>
      </div>

      <section aria-labelledby="mappings-title" className="flex flex-col gap-3">
        <h4
          id="mappings-title"
          className="text-xs font-medium tracking-wide text-subtle-foreground uppercase"
        >
          Column mappings
        </h4>
        <div className="flex flex-wrap items-center gap-3">
          <div role="group" aria-label="Show" className="flex gap-1">
            {FILTERS.map(([id, label]) => (
              <Button
                key={id}
                type="button"
                size="sm"
                variant={filter === id ? "secondary" : "ghost"}
                aria-pressed={filter === id}
                onClick={() => {
                  setFilter(id);
                  setToggled({});
                }}
              >
                {label}
                <span className="text-subtle-foreground tabular-nums">
                  {counts[id]}
                </span>
              </Button>
            ))}
          </div>
          <Input
            type="search"
            aria-label="Filter by table or column"
            placeholder="Filter by table or column"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setToggled({});
            }}
            className="max-w-xs"
          />
        </div>
        {visible.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No columns match the filter.
          </p>
        )}
        {visible.map(({ table, columns }, index) => {
          const open =
            toggled[table.table_id] ?? (filtering || index < OPEN_AT_FIRST);
          return (
            <TableGroup
              key={table.table_id}
              table={table}
              columns={columns}
              open={open}
              onToggle={() =>
                setToggled((t) => ({ ...t, [table.table_id]: !open }))
              }
              onOpenPii={onOpenPii}
            />
          );
        })}
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

function Big({ children }: { children: ReactNode }) {
  return <span className="text-3xl font-semibold tabular-nums">{children}</span>;
}

function TableGroup({
  table,
  columns,
  open,
  onToggle,
  onOpenPii,
}: {
  table: StagingMappingTable;
  columns: StagingMappingColumn[];
  open: boolean;
  onToggle: () => void;
  onOpenPii: (systemId: string) => void;
}) {
  const hashed = table.columns.filter((c) => c.mapping_type === "hashed").length;
  const review = table.columns.filter((c) => reviewLabels(c).length > 0).length;
  const source = table.source_table_name
    ? [table.source_db_schema, table.source_table_name].filter(Boolean).join(".")
    : null;
  const systemId = table.system_id;
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full flex-wrap items-center gap-2 bg-surface px-3 py-2 text-start"
      >
        <ChevronDown
          aria-hidden
          className={cn("size-4 text-subtle-foreground", !open && "-rotate-90")}
        />
        <span className={cn(monoClasses, "font-medium")}>{table.table_name}</span>
        {source && (
          <span className="text-sm text-muted-foreground">
            from <span className={monoClasses}>{source}</span>
          </span>
        )}
        {table.system_name && <Badge>{table.system_name}</Badge>}
        {hashed > 0 && <Badge variant="danger">{hashed} hashed</Badge>}
        {review > 0 && <Badge variant="warning">{review} to review</Badge>}
        <span className="ms-auto text-sm text-muted-foreground tabular-nums">
          {table.columns.length} columns
        </span>
      </button>
      {open && (
        <ul className="divide-y divide-border border-t border-border">
          {columns.map((column) => (
            <li
              key={column.column_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
            >
              <span className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                {column.source_column_name ? (
                  <>
                    <span className={monoClasses}>{column.source_column_name}</span>
                    <span className={cn(monoClasses, "text-muted-foreground")}>
                      {column.source_data_type}
                    </span>
                  </>
                ) : (
                  <span className="text-muted-foreground">none</span>
                )}
                <ArrowRight aria-hidden className="size-3.5 text-subtle-foreground" />
                <span className={monoClasses}>{column.column_name}</span>
                <span className={cn(monoClasses, "text-muted-foreground")}>
                  {typeText(column.data_type)}
                </span>
              </span>
              <span className="flex flex-wrap items-center gap-2">
                {column.mapping_type === "direct" && <Badge>Direct</Badge>}
                {column.mapping_type === "hashed" && (
                  <>
                    <Badge variant="danger">Hashed</Badge>
                    {systemId && (
                      <a
                        href={`?${new URLSearchParams({ folder: `systems/${systemId}/pii` })}`}
                        className="text-xs font-medium text-primary hover:underline"
                        onClick={(event) => {
                          event.preventDefault();
                          onOpenPii(systemId);
                        }}
                      >
                        Change in PII
                      </a>
                    )}
                  </>
                )}
                {column.mapping_type === "system" && (
                  <Badge className="border border-border bg-transparent">
                    Audit
                  </Badge>
                )}
                {reviewLabels(column).map((label) => (
                  <Badge key={label} variant="warning">
                    {label}
                  </Badge>
                ))}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
