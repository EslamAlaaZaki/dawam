"use client";

import { Check } from "lucide-react";

import { useStageProgress, type StageStatus } from "../api/workspaces";

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
      {progress.isPending && <p className="text-sand-11">Loading…</p>}
      {progress.isError && (
        <p role="alert">
          Could not load the stage progress: {progress.error.message}
        </p>
      )}
      {progress.isSuccess && (
        <>
          {progress.data.source_analysis.length === 0 && (
            <p className="mb-2 text-sand-11">
              No Source Systems yet, so there is no Source Analysis to show.
            </p>
          )}
          <ol className="m-0 flex list-none flex-wrap overflow-hidden rounded-lg border border-line bg-sand-1 p-0">
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
      className={`flex flex-[1_1_120px] items-center gap-2 border-r border-b border-line px-3 py-2 text-sm -mb-px last:border-r-0 ${
        status === "in_progress"
          ? "bg-jade-2 font-medium text-sand-12"
          : "text-sand-11"
      }`}
    >
      <span
        aria-hidden
        className={`grid size-[18px] shrink-0 place-items-center rounded-full border-[1.5px] ${
          status === "complete"
            ? "border-accent-solid bg-accent-solid text-on-accent"
            : status === "in_progress"
              ? "border-jade-11 bg-jade-11 shadow-[inset_0_0_0_3px_var(--color-sand-1)]"
              : "border-sand-7"
        }`}
      >
        {status === "complete" && (
          <Check className="size-[11px]" strokeWidth={3} />
        )}
      </span>
      <span className="min-w-0">{label}</span>
      <span className="sr-only">{STATUS_LABELS[status]}</span>
    </li>
  );
}
