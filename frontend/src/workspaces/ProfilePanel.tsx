"use client";

import { ArrowRight, ChevronDown, Lock, Search, Shield } from "lucide-react";
import { Fragment, useState } from "react";

import {
  useImportStatus,
  useSetTopN,
  useStartProfiling,
  useTableProfile,
  type ColumnProfileView,
  type ProfilingRun,
} from "../api/profiling";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Switch } from "../components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";
import { cn } from "../lib/utils";
import { EmptyState, ErrorText, TableCard, monoClasses } from "./parts";
import { Loading } from "../shell/Loading";

const fmt = (n: number) => n.toLocaleString("en-US");

/** The red shield that marks a Protected Column (PII), with its explanation in a tooltip. */
export function ProtectedIcon() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex shrink-0 text-danger">
          <Shield className="size-3.5" aria-hidden="true" />
          <span className="sr-only">Protected</span>
        </span>
      </TooltipTrigger>
      <TooltipContent>
        Protected column (PII): min, max and top values are hidden
      </TooltipContent>
    </Tooltip>
  );
}

type Filter = "all" | "nulls" | "unique" | "protected";
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["nulls", "Has nulls"],
  ["unique", "Unique"],
  ["protected", "Protected"],
];

const isUnique = (c: ColumnProfileView) =>
  c.profile != null &&
  c.profile.row_count > 0 &&
  c.profile.distinct_count === c.profile.row_count;

const FILTER_TESTS: Record<Filter, (c: ColumnProfileView) => boolean> = {
  all: () => true,
  nulls: (c) => (c.profile?.null_pct ?? 0) > 0,
  unique: isUnique,
  protected: (c) => c.is_protected,
};

function Locked({ children }: { children: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-subtle-foreground">
      <Lock className="size-3.5" aria-hidden="true" />
      {children}
    </span>
  );
}

function Nulls({ pct }: { pct: number }) {
  if (pct >= 100) return <span className="text-warning-strong">All null</span>;
  const warn = pct > 50;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1 w-14 overflow-hidden rounded-full bg-muted">
        <span
          className={cn(
            "block h-full",
            warn ? "bg-warning" : "bg-subtle-foreground",
          )}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={cn("tabular-nums", warn && "text-warning-strong")}>
        {pct.toFixed(1)}%
      </span>
    </span>
  );
}

function TopValues({
  column,
  topN,
}: {
  column: ColumnProfileView;
  topN: boolean;
}) {
  const p = column.profile;
  if (column.is_protected) {
    return (
      <Locked>Top values are never kept for protected columns (PII).</Locked>
    );
  }
  if (!topN) {
    return (
      <p className="text-xs text-subtle-foreground">
        Top values are off for this table. Turn on Keep top values to collect
        them when the table is profiled.
      </p>
    );
  }
  if (p && isUnique(column)) {
    return (
      <p className="text-xs text-subtle-foreground">
        Every sampled value is distinct, so there are no frequent values.
      </p>
    );
  }
  const top = p?.top_values ?? [];
  if (!p || top.length === 0) {
    return (
      <p className="text-xs text-subtle-foreground">No frequent values.</p>
    );
  }
  return (
    <div className="grid max-w-xl grid-cols-[minmax(0,11rem)_minmax(5rem,1fr)_auto] items-center gap-x-3 gap-y-2 text-sm whitespace-nowrap">
      <span className="text-xs font-medium text-subtle-foreground">Value</span>
      <span className="text-xs font-medium text-subtle-foreground">Share</span>
      <span className="text-xs font-medium text-subtle-foreground">Rows</span>
      {top.map((v) => {
        const share = p.row_count > 0 ? (v.count / p.row_count) * 100 : 0;
        return (
          <Fragment key={v.value}>
            <span className={`${monoClasses} truncate`} title={v.value}>
              {v.value}
            </span>
            <span className="h-1.5 overflow-hidden rounded-full bg-muted">
              <span
                className="block h-full bg-primary"
                style={{ width: `${share}%` }}
              />
            </span>
            <span className="text-xs text-subtle-foreground tabular-nums">
              {fmt(v.count)} · {share.toFixed(1)}%
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

/** The owner's per-table top-N switch. */
function TopNSwitch({
  checked,
  pending,
  onChange,
}: {
  checked: boolean;
  pending: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm font-medium">
      <Switch
        checked={checked}
        disabled={pending}
        onCheckedChange={onChange}
      />
      Keep top values
    </label>
  );
}

/**
 * A table's profile (spec stories 55-57): per column the sampled row count, null %,
 * distinct count, min and max, length and detected patterns, and top values when an owner
 * switched them on for the table. Protected Columns show no min/max or top values. Only
 * owners switch top-N, and switching it on starts profiling to collect the values (for
 * a user who may profile); otherwise profiling starts from the table page's header. A system built from
 * a Schema Import cannot be profiled, and says why. `keys` flags the table's PK and FK
 * columns (from the Source Schema already on the page); `rowEstimate` is its row estimate.
 */
export function ProfilePanel({
  workspace,
  systemId,
  tableId,
  keys = {},
  rowEstimate = null,
  onProfilingStarted,
}: {
  workspace: Workspace;
  systemId: string;
  tableId: string;
  keys?: Record<string, { pk: boolean; fk: boolean }>;
  rowEstimate?: number | null;
  onProfilingStarted?: (run: ProfilingRun) => void;
}) {
  const profile = useTableProfile(workspace.id, systemId, tableId);
  const importStatus = useImportStatus(workspace.id, systemId);
  const topN = useSetTopN(workspace.id, systemId, tableId);
  const start = useStartProfiling(workspace.id, systemId);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());

  const unavailable =
    importStatus.data?.imported === true && !importStatus.data.has_connection;
  const canSwitch = allows(workspace, "source_table.top_n");
  const canProfile =
    allows(workspace, "source_system.profile") &&
    importStatus.isSuccess &&
    !unavailable;

  // Turning top values on saves the switch, then profiles the table to collect them.
  const setTopN = (enabled: boolean) => {
    start.reset();
    topN.mutate(enabled, {
      onSuccess: () => {
        if (!enabled || !canProfile) return;
        start.mutate([tableId], {
          onSuccess: (job) => {
            onProfilingStarted?.({ jobId: job.job_id, tableIds: [tableId] });
            toast.success(
              "Top values switched on. Profiling started to collect them; follow it under Background jobs.",
            );
          },
        });
      },
    });
  };

  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const data = profile.isSuccess ? profile.data : null;
  const profiled = data != null && data.profiled_at != null;
  const nProtected = data?.columns.filter((c) => c.is_protected).length ?? 0;
  const capped = data?.columns.find((c) => c.profile?.sampled)?.profile;
  const needle = query.trim().toLowerCase();
  const visible =
    data?.columns.filter(
      (c) =>
        FILTER_TESTS[filter](c) &&
        (!needle || c.name.toLowerCase().includes(needle)),
    ) ?? [];

  return (
    <section aria-label="Profile" className="flex flex-col gap-3">
      {unavailable && (
        <p role="note" className="text-muted-foreground">
          Profiling is unavailable: this Source System was built from a Schema
          Import and has no live Connection to read data from. An owner adds a
          Connection to enable it.
        </p>
      )}
      {topN.isError && <ErrorText>{topN.error.message}</ErrorText>}
      {start.isError && <ErrorText>{start.error.message}</ErrorText>}
      {profile.isPending && (
        <Loading label="Loading the profile…" className="p-0" />
      )}
      {profile.isError && (
        <ErrorText>
          Could not load the profile: {profile.error.message}
        </ErrorText>
      )}
      {data && !profiled && (
        <>
          {canSwitch && (
            <TopNSwitch
              checked={data.top_n_enabled}
              pending={topN.isPending}
              onChange={setTopN}
            />
          )}
          <EmptyState>This table has not been profiled yet.</EmptyState>
        </>
      )}
      {data && profiled && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground tabular-nums">
              <span>
                <span className="mr-1 text-xs font-medium text-subtle-foreground">
                  Sampled
                </span>
                {data.row_count == null ? (
                  "rows unknown"
                ) : (
                  <>
                    <b className="font-medium text-foreground">
                      {fmt(data.row_count)}
                    </b>
                    {rowEstimate != null && <> of ~{fmt(rowEstimate)}</>} rows
                  </>
                )}
              </span>
              <span>
                <span className="mr-1 text-xs font-medium text-subtle-foreground">
                  Columns
                </span>
                <b className="font-medium text-foreground">
                  {data.columns.length}
                </b>
              </span>
              {nProtected > 0 && (
                <span className="inline-flex items-center gap-1">
                  <Shield className="size-3.5 text-danger" aria-hidden="true" />
                  <b className="font-medium text-foreground">{nProtected}</b>{" "}
                  protected
                </span>
              )}
              {capped && (
                <Badge variant="warning">
                  Sample capped at {fmt(capped.row_cap)} rows
                </Badge>
              )}
            </div>
            {canSwitch && (
              <TopNSwitch
                checked={data.top_n_enabled}
                pending={topN.isPending}
                onChange={setTopN}
              />
            )}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div
              role="group"
              aria-label="Filter columns"
              className="inline-flex max-w-full overflow-x-auto rounded-md border border-control-edge"
            >
              {FILTERS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                  className={cn(
                    "h-7 border-l border-control-edge px-2.5 text-xs font-medium whitespace-nowrap first:border-l-0",
                    filter === key
                      ? "bg-muted text-foreground"
                      : "bg-surface-raised text-muted-foreground",
                  )}
                >
                  {label}{" "}
                  <span className="font-normal text-subtle-foreground tabular-nums">
                    {data.columns.filter(FILTER_TESTS[key]).length}
                  </span>
                </button>
              ))}
            </div>
            <div className="relative w-56 max-w-full">
              <Search
                className="pointer-events-none absolute top-2 left-2 size-3.5 text-subtle-foreground"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Filter columns"
                aria-label="Filter columns by name"
                className="h-7 pl-7 text-sm"
              />
            </div>
          </div>
          <TableCard>
            <Table aria-label="Column profiles">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8 pr-0">
                    <span className="sr-only">Top values</span>
                  </TableHead>
                  <TableHead>Column</TableHead>
                  <TableHead>Nulls</TableHead>
                  <TableHead className="text-right">Distinct</TableHead>
                  <TableHead>Range</TableHead>
                  <TableHead>Length</TableHead>
                  <TableHead>Patterns</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="text-center text-muted-foreground"
                    >
                      No columns match.
                    </TableCell>
                  </TableRow>
                )}
                {visible.map((column) => {
                  const p = column.profile;
                  const expanded = open.has(column.column_id);
                  const key = keys[column.name];
                  const tag =
                    p && isUnique(column)
                      ? "Unique"
                      : p?.distinct_count === 1
                        ? "Constant"
                        : null;
                  return (
                    <Fragment key={column.column_id}>
                      <TableRow>
                        <TableCell className="w-8 py-2 pr-0">
                          <button
                            type="button"
                            aria-expanded={expanded}
                            aria-label={`Top values of ${column.name}`}
                            onClick={() => toggle(column.column_id)}
                            className="grid size-6 place-items-center rounded-md text-subtle-foreground hover:bg-muted hover:text-foreground"
                          >
                            <ChevronDown
                              className={cn(
                                "size-4 transition-transform",
                                !expanded && "-rotate-90",
                              )}
                              aria-hidden="true"
                            />
                          </button>
                        </TableCell>
                        <TableCell className="py-2">
                          <div className="flex items-center gap-1.5">
                            <span
                              className={`${monoClasses} max-w-64 truncate`}
                              title={column.name}
                            >
                              {column.name}
                            </span>
                            {column.is_protected && <ProtectedIcon />}
                            {key?.pk && <Badge variant="pk">PK</Badge>}
                            {key?.fk && <Badge variant="fk">FK</Badge>}
                          </div>
                          {column.data_type && (
                            <div
                              className={`${monoClasses} text-xs text-subtle-foreground`}
                            >
                              {column.data_type}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="py-2">
                          {p && <Nulls pct={p.null_pct} />}
                        </TableCell>
                        <TableCell className="py-2 text-right">
                          {p && (
                            <span className="inline-flex items-center justify-end gap-2">
                              {tag && <Badge>{tag}</Badge>}
                              <span className="tabular-nums">
                                {p.distinct_count == null ? (
                                  <span className="text-subtle-foreground">
                                    —
                                  </span>
                                ) : (
                                  fmt(p.distinct_count)
                                )}
                              </span>
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="py-2">
                          {column.is_protected ? (
                            <Locked>Hidden</Locked>
                          ) : (
                            p && (
                              <span
                                className={`${monoClasses} inline-flex items-center gap-1.5`}
                              >
                                <span>{p.min}</span>
                                <ArrowRight
                                  className="size-3 text-subtle-foreground"
                                  aria-label="to"
                                />
                                <span>{p.max}</span>
                              </span>
                            )
                          )}
                        </TableCell>
                        <TableCell className="py-2 tabular-nums">
                          {p?.max_len != null ? (
                            `avg ${p.avg_len?.toFixed(1)} · max ${p.max_len}`
                          ) : (
                            <span className="text-subtle-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2">
                          <span className="flex flex-wrap gap-1">
                            {p?.patterns.map((pattern) => (
                              <span
                                key={pattern}
                                className={`${monoClasses} inline-flex h-5 items-center rounded-md bg-muted px-1.5 text-xs text-muted-foreground`}
                              >
                                {pattern}
                              </span>
                            ))}
                          </span>
                        </TableCell>
                      </TableRow>
                      {expanded && (
                        <TableRow>
                          <TableCell className="bg-surface" />
                          <TableCell colSpan={6} className="bg-surface py-3">
                            <TopValues
                              column={column}
                              topN={data.top_n_enabled}
                            />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </TableCard>
        </>
      )}
    </section>
  );
}
