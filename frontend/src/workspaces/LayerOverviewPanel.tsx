"use client";

import { ArrowLeftRight, ArrowRight, CircleCheck } from "lucide-react";
import type { ReactNode } from "react";

import { PLATFORM_LABELS, useDataWarehouse } from "../api/dataWarehouse";
import {
  FACT_TYPE_LABELS,
  useDwTables,
  type DwTableSummary,
} from "../api/dwModel";
import { useCoverage } from "../api/mappings";
import { useScore, type FailedCheck } from "../api/score";
import {
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
import {
  EmptyState,
  ErrorText,
  h3Classes,
  linkClasses,
  monoClasses,
} from "./parts";

type Layer = "core" | "mart";

const LAYER_NAMES: Record<Layer, string> = { core: "Core", mart: "Mart" };

const DESCRIPTIONS: Record<Layer, string> = {
  core: "The integrated model: conformed dimensions and facts built from the Staging Tables.",
  mart: "Facts and aggregates shaped for reporting and KPIs, built from Core. They use the Core conformed dimensions directly.",
};

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

const SEVERITY_LABELS = {
  error: "Error",
  warning: "Warning",
  info: "Info",
} as const;

const KINDS = [
  { kind: "dimension", one: "dimension", many: "dimensions" },
  { kind: "fact", one: "fact", many: "facts" },
  { kind: "bridge", one: "bridge", many: "bridges" },
  { kind: "generated", one: "generated", many: "generated" },
] as const;

const TOP_CHECKS = 4;

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

const sectionLabelClasses =
  "text-xs font-medium tracking-wide text-subtle-foreground uppercase";

/**
 * A Core or Mart Layer's root page: what the Layer holds, in three cards that open its Model,
 * Mappings and Evaluation folders, and its tables. `onOpen` takes the folder's last path
 * segment.
 */
export function LayerOverviewPanel({
  workspace,
  layer,
  onOpen,
}: {
  workspace: Workspace;
  layer: Layer;
  onOpen: (folder: string) => void;
}) {
  const name = LAYER_NAMES[layer];
  const tables = useDwTables(workspace.id, layer);
  const warehouse = useDataWarehouse(workspace.id);
  const progress = useStageProgress(workspace.id);
  const score = useScore(workspace.id);
  const coverage = useCoverage(workspace.id);

  const status = progress.data?.dw_modeling.find((l) => l.layer === layer)
    ?.status;

  const header = (summary?: ReactNode) => (
    <header className="flex flex-col gap-2 border-b border-border pb-5 sm:flex-row sm:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <h3
          id="folder-title"
          className={cn(h3Classes, "flex flex-wrap items-center gap-x-2 gap-y-1")}
        >
          {name} Layer
          {status && (
            <Badge variant={STATUS_VARIANTS[status]} dot>
              {STATUS_LABELS[status]}
            </Badge>
          )}
        </h3>
        <p className="text-sm text-muted-foreground">{DESCRIPTIONS[layer]}</p>
        {summary && (
          <p className="text-sm text-muted-foreground tabular-nums">
            {summary}
          </p>
        )}
      </div>
    </header>
  );

  if (tables.isPending) {
    return <Loading />;
  }
  if (tables.isError && !tables.data) {
    return (
      <section aria-labelledby="folder-title" className="flex flex-col gap-5">
        {header()}
        <ErrorText>
          Could not load the {name} Layer: {tables.error.message}
        </ErrorText>
      </section>
    );
  }
  const tableData = tables.data ?? [];

  if (tableData.length === 0) {
    return (
      <section aria-labelledby="folder-title" className="flex flex-col gap-5">
        {header()}
        <EmptyState title={`No ${name} tables yet`}>
          Build the {name} Layer on the Model page. Its mappings and evaluation
          show here once it exists.
          <span className="mt-3 block">
            <Button type="button" onClick={() => onOpen("model")}>
              Open Model
              <ArrowRight aria-hidden />
            </Button>
          </span>
        </EmptyState>
      </section>
    );
  }

  const columns = tableData.reduce((sum, t) => sum + t.column_count, 0);
  const withIssues = tableData.filter(
    (t) => t.naming_violation_count > 0,
  ).length;
  const aggregates = tableData.filter(
    (t) => t.kind === "fact" && t.is_aggregate,
  ).length;
  const platform = warehouse.data?.target_platform
    ? PLATFORM_LABELS[warehouse.data.target_platform]
    : null;
  const schema = warehouse.data?.layer_schemas?.[layer];

  const summary = (
    <>
      {[
        plural(tableData.length, "table", "tables"),
        plural(columns, "column", "columns"),
      ].join(" · ")}
      {schema && (
        <>
          {" · schema "}
          <span className={monoClasses}>{schema}</span>
        </>
      )}
      {platform && ` · ${platform}`}
    </>
  );

  const layerCoverage = coverage.data?.layers.find((l) => l.layer === layer);
  const mapped = layerCoverage?.coverage;
  const coverageByTable = new Map(
    (layerCoverage?.tables ?? []).map((t) => [t.table_id, t.coverage.percent]),
  );
  const scoreByTable = new Map(
    (score.data?.tables ?? []).map((t) => [t.table_id, t.score]),
  );

  const layerScore = score.data?.layers.find((l) => l.layer === layer);
  const scored =
    layerScore?.scored && layerScore.score != null ? layerScore : null;
  const failed = (score.data?.failed_checks ?? []).filter(
    (c) => c.layer === layer,
  );

  const dimensions = tableData.filter(
    (t) => t.kind === "dimension" || t.kind === "generated",
  );
  const facts = tableData.filter(
    (t) => t.kind !== "dimension" && t.kind !== "generated",
  );

  const mappedCell = (t: DwTableSummary) => {
    const percent = coverageByTable.get(t.id);
    return t.kind === "generated" || percent === undefined
      ? "-"
      : `${Math.round(percent)}%`;
  };

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      {header(summary)}
      {tables.isError && (
        <ErrorText>
          Could not refresh the {name} Layer: {tables.error.message}
        </ErrorText>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card
          name="Model"
          label={`Model: ${plural(tableData.length, "table", "tables")}`}
          folder={`dw/${layer}/model`}
          onOpen={() => onOpen("model")}
        >
          <Big value={tableData.length} unit="tables" />
          <span className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground tabular-nums">
            {KINDS.map(({ kind, one, many }) => {
              const n = tableData.filter((t) => t.kind === kind).length;
              return n > 0 ? (
                <span key={kind}>
                  <b className="font-medium text-foreground">{n}</b>{" "}
                  {n === 1 ? one : many}
                </span>
              ) : null;
            })}
            {aggregates > 0 && (
              <span>
                <b className="font-medium text-foreground">{aggregates}</b>{" "}
                {aggregates === 1 ? "aggregate" : "aggregates"}
              </span>
            )}
          </span>
          {withIssues > 0 && (
            <Badge variant="warning" dot className="self-start">
              {withIssues} with naming issues
            </Badge>
          )}
        </Card>
        <Card
          name="Mappings"
          label={
            mapped
              ? `Mappings: ${Math.round(mapped.percent)}% mapped`
              : "Mappings: not available"
          }
          folder={`dw/${layer}/mappings`}
          icon={ArrowLeftRight}
          onOpen={() => onOpen("mappings")}
        >
          {mapped ? (
            <>
              <Big value={`${Math.round(mapped.percent)}%`} unit="mapped" />
              <span
                role="presentation"
                className="block h-1.5 overflow-hidden rounded-pill bg-muted"
              >
                <span
                  className="block h-full rounded-pill bg-primary"
                  style={{ width: `${mapped.percent}%` }}
                />
              </span>
              <span className="text-sm text-muted-foreground tabular-nums">
                {mapped.covered} of {mapped.total} columns · {mapped.system}{" "}
                system columns
              </span>
            </>
          ) : (
            <span className="text-md font-medium text-muted-foreground">
              Not available
            </span>
          )}
        </Card>
        <Card
          name="Evaluation"
          label={
            scored
              ? `Evaluation: score ${Math.round(scored.score ?? 0)}`
              : "Evaluation: not scored"
          }
          folder={`dw/${layer}/evaluation`}
          icon={CircleCheck}
          onOpen={() => onOpen("evaluation")}
        >
          {scored ? (
            <span className="flex items-center gap-4">
              <Big value={Math.round(scored.score ?? 0)} unit="/ 100" />
              {scored.grade && (
                <Badge variant={GRADE_VARIANTS[scored.grade]}>
                  Grade {scored.grade}
                </Badge>
              )}
            </span>
          ) : (
            <span className="text-md font-medium text-muted-foreground">
              Not scored
            </span>
          )}
          <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {SEVERITIES.map(({ severity, one, many, variant }) => {
              const n = failed.filter((c) => c.severity === severity).length;
              return n > 0 ? (
                <Badge key={severity} variant={variant} dot>
                  {plural(n, one, many)}
                </Badge>
              ) : null;
            })}
          </span>
        </Card>
      </div>
      {dimensions.length > 0 && (
        <Section id="dimensions-title" title="Dimensions">
          <TableBox labelledBy="dimensions-title">
            <TableHeader>
              <TableRow>
                <TableHead>Table</TableHead>
                <TableHead>SCD</TableHead>
                <TableHead>Conformed</TableHead>
                <TableHead className="text-end">Columns</TableHead>
                <TableHead className="text-end">Mapped</TableHead>
                <TableHead className="text-end">Naming issues</TableHead>
                <TableHead className="text-end">Score</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {dimensions.map((t) => {
                const tableScore = scoreByTable.get(t.id);
                return (
                  <TableRow key={t.id}>
                    <TableCell className={monoClasses}>
                      {t.name}
                      {t.kind === "generated" && (
                        <Badge className="ms-2">Generated</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {t.scd_type != null ? (
                        <Badge variant={t.scd_type === 2 ? "info" : "neutral"}>
                          Type {t.scd_type}
                        </Badge>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell>
                      {t.is_conformed ? (
                        <Badge variant="success">Conformed</Badge>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {t.column_count}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {mappedCell(t)}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {t.naming_violation_count}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {tableScore === undefined ? "-" : Math.round(tableScore)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </TableBox>
        </Section>
      )}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5 lg:items-start">
        {facts.length > 0 && (
        <Section
          id="facts-title"
          title={layer === "core" ? "Facts and bridges" : "Facts"}
          className={failed.length > 0 ? "lg:col-span-3" : "lg:col-span-5"}
        >
          <TableBox labelledBy="facts-title">
            <TableHeader>
              <TableRow>
                <TableHead>Table</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Grain</TableHead>
                <TableHead className="text-end">Columns</TableHead>
                <TableHead className="text-end">Mapped</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {facts.map((t) => (
                <TableRow key={t.id}>
                  <TableCell className={monoClasses}>
                    {t.name}
                    {t.is_aggregate && <Badge className="ms-2">Aggregate</Badge>}
                  </TableCell>
                  <TableCell>
                    {t.kind === "bridge"
                      ? "Bridge"
                      : t.fact_type
                        ? FACT_TYPE_LABELS[t.fact_type]
                        : "-"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {t.grain || "-"}
                  </TableCell>
                  <TableCell className="text-end tabular-nums">
                    {t.column_count}
                  </TableCell>
                  <TableCell className="text-end tabular-nums">
                    {mappedCell(t)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </TableBox>
        </Section>
        )}
        {failed.length > 0 && (
          <Section
            id="checks-title"
            title="Top failed checks"
            className={facts.length > 0 ? "lg:col-span-2" : "lg:col-span-5"}
            action={
              <a
                href={`?${new URLSearchParams({ folder: `dw/${layer}/evaluation` })}`}
                onClick={(event) => {
                  event.preventDefault();
                  onOpen("evaluation");
                }}
                className={cn(linkClasses, "text-xs no-underline")}
              >
                All in Evaluation
              </a>
            }
          >
            <ul className="m-0 flex list-none flex-col rounded-pill border border-border bg-surface-raised p-4">
              {failed.slice(0, TOP_CHECKS).map((c) => (
                <FailedCheckItem key={`${c.check_code}-${c.object_id}`} check={c} />
              ))}
            </ul>
          </Section>
        )}
      </div>
    </section>
  );
}

function Section({
  id,
  title,
  action,
  className,
  children,
}: {
  id: string;
  title: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className={cn("flex min-w-0 flex-col gap-3", className)}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h4 id={id} className={sectionLabelClasses}>
          {title}
        </h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function TableBox({
  labelledBy,
  children,
}: {
  labelledBy: string;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-pill border border-border">
      <Table aria-labelledby={labelledBy}>{children}</Table>
    </div>
  );
}

function FailedCheckItem({ check }: { check: FailedCheck }) {
  const { variant } = SEVERITIES.find((s) => s.severity === check.severity) ?? {
    variant: "neutral" as const,
  };
  return (
    <li className="flex items-start gap-3 border-b border-border py-2.5 first:pt-0 last:border-b-0 last:pb-0">
      <Badge variant={variant} dot>
        {SEVERITY_LABELS[check.severity]}
      </Badge>
      <span className="flex min-w-0 flex-col gap-0.5 text-sm">
        <span className={monoClasses}>{check.object_name}</span>
        <span className="text-muted-foreground">{check.message}</span>
      </span>
    </li>
  );
}
