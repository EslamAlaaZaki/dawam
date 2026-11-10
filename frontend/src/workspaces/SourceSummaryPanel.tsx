"use client";

import {
  ArrowRight,
  Database,
  FileText,
  Link2,
  ShieldAlert,
  Table2,
  Waves,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { useSourceSummary, type SourceSummary } from "../api/sourceSummary";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { buttonVariants } from "../components/ui/button";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { ErrorText } from "./parts";
import { SystemDetails } from "./SourceSystemPanels";

const STATUS_LABELS = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
} as const;

const STATUS_VARIANTS = {
  not_started: "neutral",
  in_progress: "accent",
  complete: "success",
} as const;

const sectionLabelClasses =
  "text-xs font-medium tracking-wide text-subtle-foreground uppercase";

/**
 * A Source System's root page: its details, then how complete its analysis is (spec
 * story 63). Every number tile links to the folder where that work happens.
 */
export function SourceSummaryPanel({
  workspace,
  system,
  reload,
  onOpen,
}: {
  workspace: Workspace;
  system: SourceSystem;
  reload: () => void;
  /** Opens one of this system's folders, by its last path segment. */
  onOpen: (folder: string) => void;
}) {
  const summary = useSourceSummary(workspace.id, system.id);
  const folderHref = (folder: string) =>
    `?${new URLSearchParams({ folder: `systems/${system.id}/${folder}` })}`;

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      <header className="flex flex-col gap-2 border-b border-border pb-5">
        <h1
          id="folder-title"
          className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xl font-semibold tracking-tight text-balance text-foreground"
        >
          <span className="min-w-0 break-words">{system.name}</span>{" "}
          <span className="font-normal text-subtle-foreground">/</span>{" "}
          <span className="font-medium text-muted-foreground">Summary</span>
          {summary.isSuccess && (
            <Badge variant={STATUS_VARIANTS[summary.data.status]} dot>
              {STATUS_LABELS[summary.data.status]}
            </Badge>
          )}
        </h1>
        <p className="text-sm text-muted-foreground">
          How complete the analysis of this source is.
        </p>
      </header>
      <SystemDetails workspace={workspace} system={system} reload={reload} />
      <div
        role="region"
        aria-labelledby="source-summary-title"
        className="flex flex-col gap-6"
      >
        <h2 id="source-summary-title" className="sr-only">
          Source summary
        </h2>
        {summary.isPending && <Loading className="p-0" />}
        {summary.isError && (
          <ErrorText>
            Could not load the source summary: {summary.error.message}
          </ErrorText>
        )}
        {summary.isSuccess &&
          (summary.data.has_snapshot ? (
            <SummaryTiles
              data={summary.data}
              folderHref={folderHref}
              onOpen={onOpen}
            />
          ) : (
            <div className="flex flex-col items-center gap-3 rounded-pill border border-dashed border-border-strong px-6 py-10 text-center">
              <span className="grid size-10 place-items-center rounded-pill bg-muted text-muted-foreground">
                <Database className="size-5" aria-hidden />
              </span>
              <h3 className="text-base font-medium text-foreground">
                No snapshot yet
              </h3>
              <p className="max-w-prose text-sm text-muted-foreground">
                No Snapshot yet. Extract the schema or import one to see this
                system&apos;s numbers.
              </p>
              <a
                href={folderHref("source-schema")}
                onClick={(event) => {
                  event.preventDefault();
                  onOpen("source-schema");
                }}
                className={buttonVariants()}
              >
                Go to Source Schema
                <ArrowRight aria-hidden />
              </a>
            </div>
          ))}
      </div>
    </section>
  );
}

function SummaryTiles({
  data,
  folderHref,
  onOpen,
}: {
  data: SourceSummary;
  folderHref: (folder: string) => string;
  onOpen: (folder: string) => void;
}) {
  const total = data.table_count;
  const tile = (
    folder: string,
    folderName: string,
    label: string,
    name: string,
    icon: LucideIcon,
    body: ReactNode,
  ) => (
    <Tile
      href={folderHref(folder)}
      onOpen={() => onOpen(folder)}
      folderName={folderName}
      label={label}
      name={name}
      icon={icon}
    >
      {body}
    </Tile>
  );

  return (
    <>
      <section aria-labelledby="coverage-title" className="flex flex-col gap-3">
        <h2 id="coverage-title" className={sectionLabelClasses}>
          Coverage
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {tile(
            "source-schema",
            "Source Schema",
            "Tables",
            `Tables: ${total}`,
            Table2,
            <>
              <Big value={String(total)} unit="tables and views" />
              <Bar percent={100} />
            </>,
          )}
          {tile(
            "source-schema",
            "Source Schema",
            "Documented",
            `Documented: ${data.documented_pct}%, ${data.documented_tables} of ${total} tables`,
            FileText,
            <>
              <Big
                value={`${data.documented_pct}%`}
                unit={`${data.documented_tables} of ${total} tables`}
              />
              <Bar percent={data.documented_pct} />
            </>,
          )}
          {tile(
            "profiling",
            "Profiling",
            "Profiled",
            `Profiled: ${data.profiled_pct}%, ${data.profiled_tables} of ${total} tables`,
            Waves,
            <>
              <Big
                value={`${data.profiled_pct}%`}
                unit={`${data.profiled_tables} of ${total} tables`}
              />
              <Bar percent={data.profiled_pct} />
            </>,
          )}
        </div>
      </section>
      <section aria-labelledby="findings-title" className="flex flex-col gap-3">
        <h2 id="findings-title" className={sectionLabelClasses}>
          Findings
        </h2>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {tile(
            "er-diagram",
            "ER diagram",
            "Relationships found",
            `Relationships found: ${data.relationships_found} found, ${data.relationships_to_review} to review`,
            Link2,
            <Finding
              found={data.relationships_found}
              toReview={data.relationships_to_review}
              reviewedClass="bg-success"
            />,
          )}
          {tile(
            "pii",
            "PII",
            "PII found",
            `PII found: ${data.pii_found} found, ${data.pii_to_review} to review`,
            ShieldAlert,
            <Finding
              found={data.pii_found}
              toReview={data.pii_to_review}
              reviewedClass="bg-danger"
              pii
            />,
          )}
        </div>
        <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-subtle-foreground">
          <Legend swatch="bg-success">Reviewed (relationships)</Legend>
          <Legend swatch="bg-danger">Reviewed (PII)</Legend>
          <Legend swatch="bg-warning">To review</Legend>
        </ul>
      </section>
    </>
  );
}

function Tile({
  href,
  onOpen,
  folderName,
  label,
  name,
  icon: Icon,
  children,
}: {
  href: string;
  onOpen: () => void;
  folderName: string;
  label: string;
  name: string;
  icon: LucideIcon;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      aria-label={`${name}. Open ${folderName}`}
      onClick={(event) => {
        event.preventDefault();
        onOpen();
      }}
      className="group flex min-w-0 flex-col gap-3 rounded-pill border border-border bg-surface-raised p-4 text-foreground transition-colors duration-120 hover:border-border-strong hover:bg-surface"
    >
      <span className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-muted-foreground [&_svg]:size-4 [&_svg]:text-subtle-foreground">
          <Icon aria-hidden />
          {label}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap text-subtle-foreground transition-colors duration-120 group-hover:text-primary [&_svg]:size-3.5">
          {folderName}
          <ArrowRight aria-hidden />
        </span>
      </span>
      {children}
    </a>
  );
}

function Big({
  value,
  unit,
  children,
}: {
  value: string;
  unit: string;
  children?: ReactNode;
}) {
  return (
    <span className="flex items-baseline gap-2 text-3xl font-semibold tabular-nums">
      {value}
      {children}
      <small className="text-sm font-normal text-muted-foreground">{unit}</small>
    </span>
  );
}

function Bar({ percent }: { percent: number }) {
  return (
    <span
      aria-hidden
      className="block h-1.5 overflow-hidden rounded-full bg-muted"
    >
      <span
        className={cn(
          "block h-full rounded-full",
          percent === 100 ? "bg-success" : "bg-primary",
        )}
        style={{ width: `${percent}%` }}
      />
    </span>
  );
}

function Finding({
  found,
  toReview,
  reviewedClass,
  pii = false,
}: {
  found: number;
  toReview: number;
  reviewedClass: string;
  pii?: boolean;
}) {
  const reviewed = found - toReview;
  return (
    <>
      <Big value={String(found)} unit="found">
        {pii && <Badge variant="pii">PII</Badge>}
      </Big>
      {found === 0 ? (
        <Bar percent={0} />
      ) : (
        <span aria-hidden className="flex h-1.5 gap-1">
          <span
            className={cn("rounded-full", reviewedClass)}
            style={{ flex: reviewed }}
          />
          <span className="rounded-full bg-warning" style={{ flex: toReview }} />
        </span>
      )}
      <span className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        {found === 0 ? (
          <Badge>None found</Badge>
        ) : toReview > 0 ? (
          <Badge variant="warning" dot>
            {toReview} to review
          </Badge>
        ) : (
          <Badge variant="success" dot>
            All reviewed
          </Badge>
        )}
        <span className="tabular-nums">{reviewed} reviewed</span>
      </span>
    </>
  );
}

function Legend({ swatch, children }: { swatch: string; children: ReactNode }) {
  return (
    <li className="inline-flex items-center gap-1.5">
      <span aria-hidden className={cn("size-2 rounded-sm", swatch)} />
      {children}
    </li>
  );
}
