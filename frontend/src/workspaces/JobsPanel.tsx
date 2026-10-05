"use client";

import { isActive, useCancelJob, useJobs, type Job, type JobStatus } from "../api/jobs";
import { useMe } from "../api/queries";
import { allows, type Workspace } from "../api/workspaces";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

/**
 * The Workspace's background jobs (spec story 46): each one's status, progress and log.
 * Members cancel the jobs they started; owners cancel anyone's.
 */
export function JobsPanel({ workspace }: { workspace: Workspace }) {
  const jobs = useJobs(workspace.id);
  const me = useMe();
  const cancel = useCancelJob(workspace.id);

  function canCancel(job: Job): boolean {
    return (
      isActive(job) &&
      (allows(workspace, "job.cancel_any") ||
        (allows(workspace, "job.cancel_own") && me.data != null && job.created_by === me.data.id))
    );
  }

  return (
    <section className="jobs" aria-labelledby="jobs-title">
      <h3 id="jobs-title">Background jobs</h3>
      {jobs.isPending && <p>Loading…</p>}
      {jobs.isError && <p>Could not load the jobs: {jobs.error.message}</p>}
      {jobs.isSuccess && jobs.data.items.length === 0 && <p>No background jobs yet.</p>}
      {jobs.isSuccess && jobs.data.items.length > 0 && (
        <ul className="job-list">
          {jobs.data.items.map((job) => (
            <li key={job.id} aria-label={job.title}>
              <div className="job-summary">
                <span>{job.title}</span>
                <span className={`job-status job-${job.status}`}>{STATUS_LABELS[job.status]}</span>
              </div>
              <progress max={100} value={job.progress} aria-label={`${job.title} progress`}>
                {job.progress}%
              </progress>
              {job.error && <p className="form-error">{job.error}</p>}
              {job.log && (
                <details>
                  <summary>Log</summary>
                  <pre className="job-log">{job.log}</pre>
                </details>
              )}
              {canCancel(job) && (
                <button
                  type="button"
                  className="secondary"
                  disabled={cancel.isPending}
                  onClick={() => cancel.mutate(job.id)}
                >
                  Cancel job
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {cancel.isError && <p className="form-error">{cancel.error.message}</p>}
    </section>
  );
}
