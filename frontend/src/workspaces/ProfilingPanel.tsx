"use client";

import { Activity, Info, Search } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import {
  useImportStatus,
  useProfilingGrowth,
  useProfilingOverview,
  useProfilingRuns,
  useStartProfiling,
  type GrowthTable,
  type OverviewFinding,
  type OverviewTable,
  type ProfilingGrowth,
} from "../api/profiling";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { PageHeader } from "../components/PageHeader";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { EmptyState, ErrorText, TableCard, monoClasses } from "./parts";

type Status = "profiled" | "stale" | "none" | "running";

const STATUS: Record<
  Status,
  { label: string; variant: "success" | "warning" | "neutral" | "accent" }
> = {
  profiled: { label: "Profiled", variant: "success" },
  stale: { label: "Out of date", variant: "warning" },
  none: { label: "Not profiled", variant: "neutral" },
  running: { label: "Profiling…", variant: "accent" },
};

type Kind = OverviewFinding["kind"];

const KIND: Record<Kind, { label: string; variant: "warning" | "neutral" | "info" }> = {
  all_null: { label: "All null", variant: "warning" },
  mostly_null: { label: "Mostly null", variant: "warning" },
  constant: { label: "Constant", variant: "neutral" },
  unique_not_key: { label: "Unique, not a key", variant: "info" },
};

type TableFilter = "all" | "none" | "stale" | "capped" | "findings";

const TABLE_FILTERS: readonly {
  value: TableFilter;
  label: string;
  test: (t: OverviewTable, status: Status) => boolean;
}[] = [
  { value: "all", label: "All", test: () => true },
  { value: "none", label: "Not profiled", test: (_, s) => s === "none" },
  { value: "stale", label: "Out of date", test: (_, s) => s === "stale" },
  { value: "capped", label: "Sample capped", test: (t) => t.capped },
  { value: "findings", label: "With findings", test: (t) => t.findings.length > 0 },
];

const number = (n: number) => Math.round(n).toLocaleString("en-US");

/** 24 905 110 as 24.9M, 18 240 as 18.2k. */
function compact(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e6) {
    return `${(n / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  }
  if (a >= 1e3) {
    return `${(n / 1e3).toFixed(a >= 1e4 ? 0 : 1)}k`;
  }
  return String(Math.round(n));
}

const signed = (n: number) => `${n >= 0 ? "+" : ""}${compact(n)}`;

const day = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
const date = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

function statusOf(table: OverviewTable, running: Set<string>): Status {
  if (running.has(table.table_id)) {
    return "running";
  }
  if (table.profiled_at == null) {
    return "none";
  }
  return table.out_of_date ? "stale" : "profiled";
}

function detailOf(f: OverviewFinding): string {
  switch (f.kind) {
    case "all_null":
      return "Every sampled row is null";
    case "mostly_null":
      return `${f.null_pct.toFixed(1)}% null in ${number(f.row_count)} sampled rows`;
    case "constant":
      return f.value == null ? "One value only" : `One value only: '${f.value}'`;
    case "unique_not_key":
      return f.capped
        ? `Unique in the ${number(f.row_count)} sampled rows (not checked for the whole table), but there is no PK or unique constraint`
        : "Every sampled value is distinct, but there is no PK or unique constraint";
  }
}

/** A table's name: its Database Schema subtle, its own name in full. */
function QualifiedName({ prefix, name }: { prefix: string; name: string }) {
  return (
    <span className={monoClasses}>
      <span className="text-subtle-foreground">{prefix}.</span>
      <span className="font-medium text-foreground">{name}</span>
    </span>
  );
}

/** Buttons of which one is on, each with its count. */
function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; count: number }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex max-w-full self-start overflow-x-auto rounded-md border border-border-strong"
    >
      {options.map((option, i) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          className={cn(
            "h-7 cursor-pointer bg-surface-raised px-2.5 text-xs font-medium whitespace-nowrap text-muted-foreground hover:bg-muted aria-pressed:bg-muted aria-pressed:text-foreground",
            i > 0 && "border-l border-border-strong",
          )}
          onClick={() => onChange(option.value)}
        >
          {option.label}{" "}
          <span className="font-normal text-subtle-foreground tabular-nums">
            {option.count}
          </span>
        </button>
      ))}
    </div>
  );
}

/** One of the summary cards. */
function Stat({
  label,
  value,
  unit,
  children,
}: {
  label: string;
  value: ReactNode;
  unit?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface-raised p-4">
      <span className="text-xs font-medium text-subtle-foreground">{label}</span>
      <div className="flex items-baseline gap-2">
        <span className="text-3xl font-semibold tabular-nums">{value}</span>
        {unit && (
          <span className="text-sm text-subtle-foreground tabular-nums">{unit}</span>
        )}
      </div>
      {children}
    </div>
  );
}

const footnoteClasses = "text-xs text-subtle-foreground";

/**
 * A Source System's Profiling folder (spec §6.5, story 57a): how much of the latest
 * Snapshot is profiled, what the profiles found, and how the tables grow between
 * Snapshots. Owners and editors profile every table, the out-of-date ones or a selection;
 * the per-column detail stays on each table's Profile tab in Source schema, which the
 * links open.
 */
export function ProfilingPanel({
  workspace,
  system,
  empty,
}: {
  workspace: Workspace;
  system: SourceSystem;
  /** The folder's message before the first Snapshot. */
  empty: string;
}) {
  const overview = useProfilingOverview(workspace.id, system.id);
  const growth = useProfilingGrowth(workspace.id, system.id);
  const importStatus = useImportStatus(workspace.id, system.id);
  const start = useStartProfiling(workspace.id, system.id);
  const runs = useProfilingRuns(workspace.id, system.id);
  const router = useRouter();
  const pathname = usePathname();
  const [tab, setTab] = useState("tables");
  const [tableFilter, setTableFilter] = useState<TableFilter>("all");
  const [kindFilter, setKindFilter] = useState<Kind | "all">("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  const unavailable =
    importStatus.data?.imported === true && !importStatus.data.has_connection;
  const canProfile =
    allows(workspace, "source_system.profile") &&
    importStatus.isSuccess &&
    !unavailable;

  if (overview.isPending) {
    return <Loading label="Loading the profiles…" />;
  }
  if (overview.isError) {
    return (
      <ErrorText>Could not load the profiles: {overview.error.message}</ErrorText>
    );
  }
  const latestAt = overview.data.latest_snapshot_at;
  const tables = overview.data.tables;

  const status = new Map(tables.map((t) => [t.table_id, statusOf(t, runs.profiling)]));
  const profiledCount = tables.filter((t) => t.profiled_at != null).length;
  const stale = tables.filter((t) => t.out_of_date);
  const findings = tables.flatMap((t) => t.findings.map((f) => ({ table: t, finding: f })));
  const countKind = (kind: Kind | "all") =>
    findings.filter((x) => kind === "all" || x.finding.kind === kind).length;

  const href = (tableId: string) =>
    `${pathname}?${new URLSearchParams({
      folder: `systems/${system.id}/source-schema`,
      table: tableId,
      table_tab: "profile",
    })}`;
  function openLink(tableId: string, label: string) {
    const to = href(tableId);
    return (
      <a
        href={to}
        onClick={(event) => {
          event.preventDefault();
          router.push(to);
        }}
        className="text-sm font-medium whitespace-nowrap text-primary hover:underline"
      >
        {label}
      </a>
    );
  }

  function profile(tableIds: string[], what: string) {
    start.mutate(tableIds, {
      onSuccess: (job) => {
        runs.follow({ jobId: job.job_id, tableIds });
        setSelected(new Set());
        toast.success(
          `Profiling started for ${what}. Follow it under Background jobs.`,
        );
      },
    });
  }

  const header = (
    <PageHeader
      title="Profiling"
      description={`Sampled profiles of every table and view in ${system.name}, what they found, and how the data grows between Snapshots.`}
      actions={
        canProfile &&
        tables.length > 0 && (
          <>
            <Button
              type="button"
              variant="secondary"
              disabled={start.isPending || stale.length === 0}
              onClick={() =>
                profile(
                  stale.map((t) => t.table_id),
                  `${stale.length} out-of-date ${stale.length === 1 ? "table" : "tables"}`,
                )
              }
            >
              Profile out-of-date tables
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={start.isPending}
              onClick={() =>
                profile(
                  tables.map((t) => t.table_id),
                  `${tables.length} tables and views`,
                )
              }
            >
              <Activity aria-hidden />
              Profile all tables
            </Button>
          </>
        )
      }
    />
  );

  if (latestAt == null) {
    return (
      <section aria-label="Profiling" className="flex flex-col gap-5">
        {header}
        <EmptyState>{empty}</EmptyState>
      </section>
    );
  }

  const needle = search.trim().toLowerCase();
  const filter = TABLE_FILTERS.find((f) => f.value === tableFilter)!;
  const visible = tables.filter(
    (t) =>
      filter.test(t, status.get(t.table_id)!) &&
      `${t.db_schema}.${t.name}`.toLowerCase().includes(needle),
  );
  const allVisibleSelected =
    visible.length > 0 && visible.every((t) => selected.has(t.table_id));
  const someVisibleSelected = visible.some((t) => selected.has(t.table_id));
  function toggle(ids: string[], on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (on) {
          next.add(id);
        } else {
          next.delete(id);
        }
      }
      return next;
    });
  }
  const visibleFindings = findings.filter(
    (x) => kindFilter === "all" || x.finding.kind === kindFilter,
  );
  const growthData = growth.data;
  const growing = growthData && growthData.snapshots.length >= 2;

  return (
    <section aria-label="Profiling" className="flex flex-col gap-5">
      {header}
      {unavailable && (
        <p role="note" className="text-muted-foreground">
          Profiling is unavailable: this Source System was built from a Schema
          Import and has no live Connection to read data from. An owner adds a
          Connection to enable it.
        </p>
      )}
      {start.isError && <ErrorText>{start.error.message}</ErrorText>}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label="Profiled"
          value={`${tables.length ? Math.round((profiledCount / tables.length) * 100) : 0}%`}
          unit={`${profiledCount} of ${tables.length} tables`}
        >
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-primary"
              style={{
                width: `${tables.length ? (profiledCount / tables.length) * 100 : 0}%`,
              }}
            />
          </div>
        </Stat>
        <Stat label="Out of date" value={stale.length}>
          <span className="text-sm text-subtle-foreground">
            Profiled before the latest Snapshot ({day(latestAt)})
          </span>
        </Stat>
        <Stat label="Findings" value={findings.length}>
          <span className="text-sm text-subtle-foreground tabular-nums">
            {countKind("all_null")} all null · {countKind("mostly_null")} mostly
            null · {countKind("unique_not_key")} possible keys
          </span>
        </Stat>
        <Stat
          label="Growth"
          value={growing ? signed(growthData.total_per_month) : "—"}
          unit={growing ? "rows / month" : undefined}
        >
          <span className="text-sm text-subtle-foreground tabular-nums">
            {growing
              ? `${compact(growthData.total_first)} → ${compact(growthData.total_latest)} rows since ${day(growthData.snapshots[0]!)}`
              : growth.isPending
                ? "Loading…"
                : "Needs 2 Snapshots"}
          </span>
        </Stat>
      </div>

      <Tabs value={tab} onValueChange={setTab} className="gap-4">
        <TabsList className="px-0" aria-label="Profiling views">
          <TabsTrigger value="tables">
            Tables
            <span className="ms-1.5 text-xs font-normal text-subtle-foreground tabular-nums">
              {tables.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="findings">
            Findings
            <span className="ms-1.5 text-xs font-normal text-subtle-foreground tabular-nums">
              {findings.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="growth">
            Data growth
            <span className="ms-1.5 text-xs font-normal text-subtle-foreground tabular-nums">
              {growthData?.tables.length ?? 0}
            </span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="tables" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Segmented
              label="Filter tables"
              value={tableFilter}
              options={TABLE_FILTERS.map((f) => ({
                value: f.value,
                label: f.label,
                count: tables.filter((t) => f.test(t, status.get(t.table_id)!)).length,
              }))}
              onChange={setTableFilter}
            />
            <div className="flex flex-wrap items-center gap-2">
              {canProfile && selected.size > 0 && (
                <Button
                  type="button"
                  size="sm"
                  disabled={start.isPending}
                  onClick={() =>
                    profile(
                      [...selected],
                      `${selected.size} selected ${selected.size === 1 ? "table" : "tables"}`,
                    )
                  }
                >
                  Profile selected ({selected.size})
                </Button>
              )}
              <div className="relative w-56 max-w-full">
                <Search
                  aria-hidden
                  className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-subtle-foreground"
                />
                <Input
                  type="search"
                  placeholder="Filter tables"
                  aria-label="Filter tables by name"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="h-7 ps-7 text-sm"
                />
              </div>
            </div>
          </div>
          {visible.length === 0 ? (
            <EmptyState>No table matches the filter.</EmptyState>
          ) : (
            <TableCard>
              <Table aria-label="Tables">
                <TableHeader>
                  <TableRow>
                    {canProfile && (
                      <TableHead className="w-8 pe-0">
                        <Checkbox
                          aria-label="Select all tables"
                          checked={
                            allVisibleSelected
                              ? true
                              : someVisibleSelected
                                ? "indeterminate"
                                : false
                          }
                          onCheckedChange={(checked) =>
                            toggle(
                              visible.map((t) => t.table_id),
                              checked === true,
                            )
                          }
                        />
                      </TableHead>
                    )}
                    <TableHead>Table</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Rows sampled</TableHead>
                    <TableHead className="text-right">Columns</TableHead>
                    <TableHead className="text-right">Findings</TableHead>
                    <TableHead>Last profiled</TableHead>
                    <TableHead>
                      <span className="sr-only">Open</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visible.map((t) => {
                    const s = STATUS[status.get(t.table_id)!];
                    const has = t.rows_sampled != null;
                    const full = `${t.db_schema}.${t.name}`;
                    return (
                      <TableRow key={t.table_id}>
                        {canProfile && (
                          <TableCell className="pe-0">
                            <Checkbox
                              aria-label={`Select ${full}`}
                              checked={selected.has(t.table_id)}
                              onCheckedChange={(checked) =>
                                toggle([t.table_id], checked === true)
                              }
                            />
                          </TableCell>
                        )}
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <QualifiedName prefix={t.db_schema} name={t.name} />
                            {t.kind === "view" && <Badge>View</Badge>}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge variant={s.variant}>{s.label}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          <div className="inline-flex items-center justify-end gap-1.5">
                            {t.capped && <Badge variant="warning">Capped</Badge>}
                            <span>{has ? number(t.rows_sampled!) : "—"}</span>
                            {has &&
                              t.row_estimate != null &&
                              t.row_estimate > t.rows_sampled! && (
                                <span className="text-subtle-foreground">
                                  of ~{compact(t.row_estimate)}
                                </span>
                              )}
                          </div>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {t.column_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          <span
                            className={
                              has && t.findings.length > 0
                                ? "font-medium text-warning-strong"
                                : "text-subtle-foreground"
                            }
                          >
                            {has ? t.findings.length : "—"}
                          </span>
                        </TableCell>
                        <TableCell className="text-muted-foreground tabular-nums">
                          {t.profiled_at ? date(t.profiled_at) : "Never"}
                        </TableCell>
                        <TableCell className="text-right">
                          {openLink(t.table_id, "Open profile")}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableCard>
          )}
        </TabsContent>

        <TabsContent value="findings" className="flex flex-col gap-3">
          <Segmented
            label="Filter findings"
            value={kindFilter}
            options={[
              { value: "all" as const, label: "All", count: countKind("all") },
              ...(Object.keys(KIND) as Kind[]).map((k) => ({
                value: k,
                label: KIND[k].label,
                count: countKind(k),
              })),
            ]}
            onChange={setKindFilter}
          />
          {visibleFindings.length === 0 ? (
            <EmptyState>
              {findings.length === 0
                ? "No findings. Profile tables to look for them."
                : "No finding of this kind."}
            </EmptyState>
          ) : (
            <TableCard>
              <Table aria-label="Findings">
                <TableHeader>
                  <TableRow>
                    <TableHead>Finding</TableHead>
                    <TableHead>Column</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Detail</TableHead>
                    <TableHead>
                      <span className="sr-only">Open</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleFindings.map(({ table: t, finding: f }) => (
                    <TableRow key={f.column_id}>
                      <TableCell>
                        <Badge variant={KIND[f.kind].variant}>
                          {KIND[f.kind].label}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <QualifiedName
                          prefix={`${t.db_schema}.${t.name}`}
                          name={f.column}
                        />
                      </TableCell>
                      <TableCell
                        className={cn(monoClasses, "text-subtle-foreground")}
                      >
                        {f.data_type}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {detailOf(f)}
                      </TableCell>
                      <TableCell className="text-right">
                        {openLink(t.table_id, "Open column")}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableCard>
          )}
          <p className={footnoteClasses}>
            Findings come from the latest profile of each table. Protected
            columns (PII) are checked for nulls only.
          </p>
        </TabsContent>

        <TabsContent value="growth" className="flex flex-col gap-3">
          {growth.isPending && <Loading className="p-0" />}
          {growth.isError && (
            <ErrorText>Could not load the growth: {growth.error.message}</ErrorText>
          )}
          {growthData &&
            (growing ? (
              <GrowthView growth={growthData} />
            ) : (
              <EmptyState>Growth needs at least 2 Snapshots.</EmptyState>
            ))}
        </TabsContent>
      </Tabs>
    </section>
  );
}

const SPARK_WIDTH = 96;
const SPARK_HEIGHT = 24;

/** The table's row estimate per Snapshot, as a small line. */
function Sparkline({ table }: { table: GrowthTable }) {
  const values = table.points
    .map((p) => p.row_estimate)
    .filter((v): v is number => v != null);
  const min = Math.min(...values);
  const range = Math.max(...values) - min || 1;
  const step = (SPARK_WIDTH - 4) / Math.max(values.length - 1, 1);
  const points = values
    .map(
      (v, i) =>
        `${(2 + i * step).toFixed(1)},${(SPARK_HEIGHT - 3 - ((v - min) / range) * (SPARK_HEIGHT - 6)).toFixed(1)}`,
    )
    .join(" ");
  return (
    <svg
      width={SPARK_WIDTH}
      height={SPARK_HEIGHT}
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      role="img"
      aria-label={`Rows from ${compact(table.first)} to ${compact(table.latest)}`}
    >
      <polyline
        points={points}
        fill="none"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        className={table.dropped_at ? "stroke-warning" : "stroke-primary"}
      />
    </svg>
  );
}

const LEFT_OUT_REASON = {
  unchanged: "no change",
  view: "a view has no row estimate",
  no_estimate: "no row estimate",
  new: "in one Snapshot only",
} as const;

/** The Data growth tab: each table's trend, sorted by rows per month. */
function GrowthView({ growth }: { growth: ProfilingGrowth }) {
  const first = growth.snapshots[0]!;
  const last = growth.snapshots[growth.snapshots.length - 1]!;
  const days = Math.round(
    (new Date(last).getTime() - new Date(first).getTime()) / 86_400_000,
  );
  const reasons = (Object.keys(LEFT_OUT_REASON) as (keyof typeof LEFT_OUT_REASON)[])
    .map((reason) => {
      const names = growth.left_out
        .filter((t) => t.reason === reason)
        .map((t) => `${t.db_schema}.${t.name}`);
      return names.length ? `${names.join(", ")} (${LEFT_OUT_REASON[reason]})` : null;
    })
    .filter((part) => part != null);
  const dropped = growth.tables.filter((t) => t.dropped_at != null);
  return (
    <>
      <div
        role="note"
        className="flex items-start gap-2.5 rounded-lg border border-border bg-surface px-3 py-2.5 text-sm text-muted-foreground"
      >
        <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-info" />
        <span>
          Estimated from the row estimates in {growth.snapshots.length} Snapshots,{" "}
          {day(first)} to {date(last)} ({days} {days === 1 ? "day" : "days"}). Row
          estimates come from database statistics, so treat these as trends, not
          exact counts. The 12-month projection assumes the same linear growth.
        </span>
      </div>
      {growth.tables.length === 0 ? (
        <EmptyState>No table changed size between the Snapshots.</EmptyState>
      ) : (
        <TableCard>
          <Table aria-label="Data growth">
            <TableHeader>
              <TableRow>
                <TableHead>Table</TableHead>
                <TableHead>Trend</TableHead>
                <TableHead className="text-right">{day(first)}</TableHead>
                <TableHead className="text-right">{day(last)}</TableHead>
                <TableHead className="text-right">Change</TableHead>
                <TableHead className="text-right">Per month</TableHead>
                <TableHead className="text-right">In 12 months</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {growth.tables.map((t) => (
                <TableRow key={t.table_id}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <QualifiedName prefix={t.db_schema} name={t.name} />
                      {t.dropped_at && (
                        <Badge variant="warning">Dropped on {day(t.dropped_at)}</Badge>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <Sparkline table={t} />
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground tabular-nums">
                    {compact(t.first)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {compact(t.latest)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {t.change_pct == null ? (
                      <span className="text-subtle-foreground">—</span>
                    ) : (
                      <span
                        className={
                          t.change_pct >= 0 ? "text-foreground" : "text-warning-strong"
                        }
                      >
                        {t.change_pct >= 0 ? "+" : ""}
                        {t.change_pct.toFixed(1)}%
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {signed(t.per_month)}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">
                    ~{compact(t.in_12_months)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {(reasons.length > 0 || dropped.length > 0) && (
        <p className={footnoteClasses}>
          {reasons.length > 0 && `Not shown: ${reasons.join(", ")}.`}
          {dropped.map(
            (t) =>
              ` ${t.db_schema}.${t.name} fell by more than a fifth on ${day(t.dropped_at!)}, so it was probably purged or truncated; its rate counts only from that point.`,
          )}
        </p>
      )}
    </>
  );
}
