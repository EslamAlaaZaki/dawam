"use client";

import { Check } from "lucide-react";

import { useStageProgress, type StageStatus } from "../api/workspaces";
import { Loading } from "../shell/Loading";
import { ErrorText } from "./parts";

const STATUS_LABELS: Record<StageStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
};

const LAYER_LABELS = {
  staging: "Staging",
  core: "Core",
  mart: "Mart",
} as const;

/** Source Analysis per Source System, KPIs and DW Modeling per Layer (spec story 37), as the page header's stepper. */
export function StageProgressPanel({ workspaceId }: { workspaceId: string }) {
  const progress = useStageProgress(workspaceId);

  return (
    <section aria-labelledby="stage-progress-title">
      <h3 id="stage-progress-title" className="sr-only">
        Stage progress
      </h3>
      {progress.isPending && <Loading className="p-0" />}
      {progress.isError && (
        <ErrorText>
          Could not load the stage progress: {progress.error.message}
        </ErrorText>
      )}
      {progress.isSuccess && (
        <>
          {progress.data.source_analysis.length === 0 && (
            <p className="mb-2 text-muted-foreground">
              No Source Systems yet, so there is no Source Analysis to show.
            </p>
          )}
          <ol className="m-0 flex list-none flex-wrap items-center gap-x-3 gap-y-2 rounded-pill border border-border bg-surface-raised px-4 py-3">
            {progress.data.source_analysis.map((system) => (
              <Stage
                key={system.system_id}
                label={`Source Analysis: ${system.name}`}
                status={system.status}
              />
            ))}
            <Stage label="KPIs" status={progress.data.kpis.status} />
            {progress.data.dw_modeling.map((layer) => (
              <Stage
                key={layer.layer}
                label={`DW Modeling: ${LAYER_LABELS[layer.layer]}`}
                status={layer.status}
              />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function Stage({ label, status }: { label: string; status: StageStatus }) {
  return (
    <li
      aria-current={status === "in_progress" ? "step" : undefined}
      className={`flex items-center gap-2 text-sm not-first:before:h-px not-first:before:w-6 not-first:before:bg-border-strong not-first:before:content-[''] ${
        status === "in_progress"
          ? "font-medium text-foreground"
          : status === "complete"
            ? "text-foreground"
            : "text-muted-foreground"
      }`}
    >
      <span
        aria-hidden
        className={`grid size-4.5 shrink-0 place-items-center rounded-full border ${
          status === "complete"
            ? "border-primary bg-primary text-primary-foreground"
            : status === "in_progress"
              ? "border-primary bg-primary-subtle"
              : "border-border-strong bg-surface-raised"
        }`}
      >
        {status === "complete" && <Check className="size-3" strokeWidth={3} />}
        {status === "in_progress" && (
          <span className="size-1.5 rounded-full bg-primary" />
        )}
      </span>
      <span className="min-w-0">{label}</span>
      <span className="sr-only">{STATUS_LABELS[status]}</span>
    </li>
  );
}
