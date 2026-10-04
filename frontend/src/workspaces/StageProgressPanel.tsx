"use client";

import { useStageProgress, type StageStatus } from "../api/workspaces";

const STATUS_LABELS: Record<StageStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
};

const LAYER_LABELS = { staging: "Staging", core: "Core", mart: "Mart" } as const;

/** Source Analysis per Source System, KPIs and DW Modeling per Layer (spec story 37). */
export function StageProgressPanel({ workspaceId }: { workspaceId: string }) {
  const progress = useStageProgress(workspaceId);

  return (
    <section className="stage-progress" aria-labelledby="stage-progress-title">
      <h3 id="stage-progress-title">Stage progress</h3>
      {progress.isPending && <p>Loading…</p>}
      {progress.isError && (
        <p role="alert">Could not load the stage progress: {progress.error.message}</p>
      )}
      {progress.isSuccess && (
        <>
          {progress.data.source_analysis.length === 0 && (
            <p>No Source Systems yet, so there is no Source Analysis to show.</p>
          )}
          <ul className="stage-list">
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
          </ul>
        </>
      )}
    </section>
  );
}

function Stage({ label, status }: { label: string; status: StageStatus }) {
  return (
    <li>
      <span>{label}</span>
      <span className={`stage-status stage-${status}`}>{STATUS_LABELS[status]}</span>
    </li>
  );
}
