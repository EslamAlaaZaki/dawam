"use client";

import {
  ArrowRight,
  FileCode,
  Folder,
  Gauge,
  GitBranch,
  Settings,
  Target,
  type LucideIcon,
} from "lucide-react";
import { Fragment, type ReactNode } from "react";

import { PLATFORM_LABELS, useDataWarehouse } from "../api/dataWarehouse";
import { useDwTables, type DwTableSummary } from "../api/dwModel";
import { useAreaFiles } from "../api/files";
import { useKpiList } from "../api/kpis";
import { useCoverage } from "../api/mappings";
import { useScore } from "../api/score";
import {
  allows,
  useStageProgress,
  type StageStatus,
  type Workspace,
} from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { Big, Card } from "./overviewParts";
import { ErrorText, h3Classes, monoClasses } from "./parts";

type Layer = "staging" | "core" | "mart";

const LAYERS: { layer: Layer; name: string }[] = [
  { layer: "staging", name: "Staging" },
  { layer: "core", name: "Core" },
  { layer: "mart", name: "Mart" },
];

const STATUS_LABELS: Record<StageStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
};

const STATUS_VARIANTS = {
  not_started: "neutral",
  in_progress: "accent",
  complete: "success",
} as const;

const GRADE_VARIANTS = {
  A: "success",
  B: "success",
  C: "warning",
  D: "danger",
  F: "danger",
} as const;

const SEVERITIES = [
  { severity: "error", one: "error", many: "errors", variant: "danger" },
  { severity: "warning", one: "warning", many: "warnings", variant: "warning" },
  { severity: "info", one: "info", many: "info", variant: "neutral" },
] as const;

const WEEKDAY_LABELS: Record<string, string> = {
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
  sunday: "Sun",
};

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

const sectionLabelClasses =
  "text-xs font-medium tracking-wide text-subtle-foreground uppercase";

/** "Sat and Sun", "Fri, Sat and Sun". */
function listDays(days: string[]): string {
  const labels = Object.keys(WEEKDAY_LABELS)
    .filter((d) => days.includes(d))
    .map((d) => WEEKDAY_LABELS[d]!);
  if (labels.length <= 1) {
    return labels[0] ?? "none";
  }
  return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/** Setup values, each in the mono font, between " · ". */
function Mono({ values }: { values: string[] }) {
  return (
    <>
      {values.map((v, i) => (
        <Fragment key={i}>
          {i > 0 && " · "}
          <span className={monoClasses}>{v}</span>
        </Fragment>
      ))}
    </>
  );
}

/**
 * The Data Warehouse's root page once it is set up: the setup at a glance, the score, one row
 * per Layer and links to the rest of the folder. `onOpen` takes a folder id below `dw`
 * (`score`, `core`, `kpis`, ...); `onEdit` opens the setup dialog.
 */
export function DataWarehouseOverviewPanel({
  workspace,
  onOpen,
  onEdit,
}: {
  workspace: Workspace;
  onOpen: (folder: string) => void;
  onEdit: () => void;
}) {
  const warehouse = useDataWarehouse(workspace.id);
  const staging = useDwTables(workspace.id, "staging");
  const core = useDwTables(workspace.id, "core");
  const mart = useDwTables(workspace.id, "mart");
  const progress = useStageProgress(workspace.id);
  const score = useScore(workspace.id);
  const coverage = useCoverage(workspace.id);
  const kpis = useKpiList(
    workspace.id,
    { systemId: null, workspaceWide: true },
    allows(workspace, "kpi.view_review") ? "all" : "catalog",
  );
  const files = useAreaFiles(workspace.id, "data-warehouse");

  const tablesOf = { staging, core, mart };

  if (
    warehouse.isPending ||
    staging.isPending ||
    core.isPending ||
    mart.isPending
  ) {
    return <Loading />;
  }
  const loadError = [staging, core, mart].find((q) => q.isError && !q.data);

  const dw = warehouse.data;
  const platform = dw?.target_platform
    ? PLATFORM_LABELS[dw.target_platform]
    : null;
  const rows = LAYERS.map(({ layer, name }) => {
    const tables: DwTableSummary[] = tablesOf[layer].data ?? [];
    return {
      layer,
      name,
      tables: tables.length,
      columns: tables.reduce((sum, t) => sum + t.column_count, 0),
      issues: tables.filter((t) => t.naming_violation_count > 0).length,
    };
  });
  const totalTables = rows.reduce((sum, r) => sum + r.tables, 0);
  const totalColumns = rows.reduce((sum, r) => sum + r.columns, 0);
  const percent = coverage.data?.coverage.percent;
  const setUpAt = dw?.set_up_at
    ? new Date(dw.set_up_at).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : null;
  const summary = [
    platform,
    "3 Layers",
    plural(totalTables, "table", "tables"),
    plural(totalColumns, "column", "columns"),
    percent !== undefined
      ? `${Math.round(percent)}% of Core and Mart columns mapped`
      : null,
    setUpAt ? `set up ${setUpAt}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const scoreOf = (layer: Layer) => {
    const l = score.data?.layers.find((x) => x.layer === layer);
    return l?.scored && l.score != null ? l : null;
  };
  const overall = score.data && score.data.score != null ? score.data : null;
  const failed = (score.data?.failed_checks ?? []).filter(
    (c) => c.layer === "core" || c.layer === "mart",
  );

  const naming = dw?.naming_rules;
  const schemas = dw?.layer_schemas;
  const dates = dw?.date_dimension;

  const kpiRows = kpis.data?.kpis ?? [];
  const accepted = kpiRows.filter(
    (k) => k.review_state === "live" || k.review_state === "accepted",
  ).length;
  const suggested =
    kpiRows.filter((k) => k.review_state === "suggested").length +
    (kpis.data?.ruleSuggestions.length ?? 0);

  const tiles: {
    folder: string;
    name: string;
    icon: LucideIcon;
    text: string;
  }[] = [
    {
      folder: "kpis",
      name: "KPIs",
      icon: Target,
      text: kpis.data ? `${accepted} accepted · ${suggested} suggested` : "",
    },
    {
      folder: "lineage",
      name: "Lineage",
      icon: GitBranch,
      text: "Trace any column to its source",
    },
    {
      folder: "files",
      name: "Files",
      icon: Folder,
      text: files.data ? plural(files.data.length, "file", "files") : "",
    },
    {
      folder: "ddl",
      name: "DDL",
      icon: FileCode,
      text: platform ? `Generate for ${platform}` : "Generate",
    },
  ];

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <h3 id="folder-title" className={h3Classes}>
            Data Warehouse
          </h3>
          <p className="text-sm text-muted-foreground">
            The target model for every Source System: Staging, then Core, then
            Mart.
          </p>
          <p className="text-sm text-muted-foreground tabular-nums">
            {summary}
          </p>
        </div>
        <Button type="button" onClick={onEdit}>
          <Settings aria-hidden />
          Edit setup
        </Button>
      </header>
      {loadError?.isError && (
        <ErrorText>
          Could not load the Layers: {loadError.error.message}
        </ErrorText>
      )}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-5 lg:items-start">
        <div className="flex min-w-0 flex-col lg:col-span-3">
          <Card
            name="Data Warehouse score"
            label={
              overall
                ? `Score ${Math.round(overall.score ?? 0)}${overall.grade ? `, grade ${overall.grade}` : ""}`
                : "Not scored"
            }
            folder="dw/score"
            icon={Gauge}
            openLabel="Open Score"
            onOpen={() => onOpen("score")}
          >
            {overall ? (
              <span className="flex items-center gap-4">
                <Big value={Math.round(overall.score ?? 0)} unit="/ 100" />
                {overall.grade && (
                  <Badge variant={GRADE_VARIANTS[overall.grade]}>
                    Grade {overall.grade}
                  </Badge>
                )}
              </span>
            ) : (
              <span className="text-md font-medium text-muted-foreground">
                Not scored
              </span>
            )}
            <span className="flex flex-col gap-2">
              {(["core", "mart"] as const).map((layer) => {
                const s = scoreOf(layer);
                return (
                  <span
                    key={layer}
                    className="grid grid-cols-[4rem_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm"
                  >
                    <span>{layer === "core" ? "Core" : "Mart"}</span>
                    <span
                      role="presentation"
                      className="block h-1.5 overflow-hidden rounded-pill bg-muted"
                    >
                      {s && (
                        <span
                          className="block h-full rounded-pill bg-primary"
                          style={{
                            width: `${Math.min(100, Math.max(0, Math.round(s.score ?? 0)))}%`,
                          }}
                        />
                      )}
                    </span>
                    <span className="text-end text-muted-foreground tabular-nums">
                      {s
                        ? `${Math.round(s.score ?? 0)} · ${s.grade}`
                        : "Not scored"}
                    </span>
                  </span>
                );
              })}
            </span>
            <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              {SEVERITIES.map(({ severity, one, many, variant }) => {
                const n = failed.filter((c) => c.severity === severity).length;
                return n > 0 ? (
                  <Badge key={severity} variant={variant} dot>
                    {plural(n, one, many)}
                  </Badge>
                ) : null;
              })}
              Core and Mart only. Staging is scored apart.
            </span>
          </Card>
        </div>
        <section
          aria-labelledby="setup-title"
          className="flex min-w-0 flex-col gap-3 rounded-pill border border-border bg-surface-raised p-4 lg:col-span-2"
        >
          <h4
            id="setup-title"
            className="flex items-center gap-2 text-sm font-medium text-muted-foreground [&_svg]:size-4 [&_svg]:text-subtle-foreground"
          >
            <Settings aria-hidden />
            Setup
          </h4>
          <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm [&_dd]:m-0 [&_dd]:min-w-0 [&_dd]:break-words [&_dt]:text-muted-foreground">
            <dt>Platform</dt>
            <dd>{platform ?? "-"}</dd>
            <dt>Schemas</dt>
            <dd>
              {schemas ? (
                <Mono values={[schemas.staging, schemas.core, schemas.mart]} />
              ) : (
                "-"
              )}
            </dd>
            <dt>Prefixes</dt>
            <dd>
              {naming ? (
                <Mono
                  values={[
                    naming.dimension_prefix,
                    naming.fact_prefix,
                    naming.bridge_prefix,
                  ]}
                />
              ) : (
                "-"
              )}
            </dd>
            <dt>Audit columns</dt>
            <dd>
              {naming ? (
                <Mono
                  values={[
                    naming.load_ts_column,
                    naming.source_system_column,
                    naming.created_ts_column,
                  ]}
                />
              ) : (
                "-"
              )}
            </dd>
            <dt>Case</dt>
            <dd>{naming?.case_style ?? "-"}</dd>
            <dt>Date dimension</dt>
            <dd className="tabular-nums">
              {dates
                ? `${dates.start_year} to ${dates.end_year}, weekend ${listDays(dates.weekend_days)}`
                : "-"}
            </dd>
          </dl>
        </section>
      </div>
      <Section id="layers-title" title="Layers">
        <div className="overflow-hidden rounded-pill border border-border">
          <Table aria-labelledby="layers-title">
            <TableHeader>
              <TableRow>
                <TableHead>Layer</TableHead>
                <TableHead>Schema</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-end">Tables</TableHead>
                <TableHead className="text-end">Columns</TableHead>
                <TableHead className="text-end">Mapped</TableHead>
                <TableHead className="text-end">Naming issues</TableHead>
                <TableHead className="text-end">Score</TableHead>
                <TableHead>
                  <span className="sr-only">Open</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const status = progress.data?.dw_modeling.find(
                  (l) => l.layer === r.layer,
                )?.status;
                const s = scoreOf(r.layer);
                const mapped = coverage.data?.layers.find(
                  (l) => l.layer === r.layer,
                )?.coverage;
                return (
                  <TableRow
                    key={r.layer}
                    onClick={() => onOpen(r.layer)}
                    className="group cursor-pointer"
                  >
                    <TableCell>{r.name}</TableCell>
                    <TableCell className={monoClasses}>
                      {schemas?.[r.layer] ?? "-"}
                    </TableCell>
                    <TableCell>
                      {status ? (
                        <Badge variant={STATUS_VARIANTS[status]} dot>
                          {STATUS_LABELS[status]}
                        </Badge>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {r.tables}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {r.columns}
                    </TableCell>
                    <TableCell
                      className={cn(
                        "text-end tabular-nums",
                        r.layer === "staging" && "text-muted-foreground",
                      )}
                    >
                      {r.layer === "staging"
                        ? "1:1"
                        : mapped
                          ? `${Math.round(mapped.percent)}%`
                          : "-"}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {r.issues}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {s ? (
                        <>
                          {Math.round(s.score ?? 0)}{" "}
                          {s.grade && (
                            <Badge variant={GRADE_VARIANTS[s.grade]}>
                              {s.grade}
                            </Badge>
                          )}
                        </>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-end">
                      <a
                        href={`?${new URLSearchParams({ folder: `dw/${r.layer}` })}`}
                        aria-label={`Open ${r.name}`}
                        onClick={(event) => event.preventDefault()}
                        className="inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap text-subtle-foreground transition-colors duration-120 group-hover:text-primary [&_svg]:size-3.5"
                      >
                        Open
                        <ArrowRight aria-hidden />
                      </a>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </Section>
      <Section id="more-title" title="Also in the Data Warehouse">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {tiles.map(({ folder, name, icon: Icon, text }) => (
            <a
              key={folder}
              href={`?${new URLSearchParams({ folder: `dw/${folder}` })}`}
              onClick={(event) => {
                event.preventDefault();
                onOpen(folder);
              }}
              className="flex min-w-0 flex-col gap-1 rounded-pill border border-border bg-surface-raised p-3 text-foreground transition-colors duration-120 hover:border-border-strong hover:bg-surface"
            >
              <b className="flex items-center gap-2 font-medium [&_svg]:size-4 [&_svg]:text-subtle-foreground">
                <Icon aria-hidden />
                {name}
              </b>
              <span className="text-sm text-muted-foreground tabular-nums">
                {text}
              </span>
            </a>
          ))}
        </div>
      </Section>
    </section>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-3">
      <h4 id={id} className={sectionLabelClasses}>
        {title}
      </h4>
      {children}
    </section>
  );
}
