"use client";

import { Loader } from "lucide-react";

import {
  isActive,
  useCancelJob,
  useJobs,
  type Job,
  type JobStatus,
} from "../api/jobs";
import { useMe } from "../api/queries";
import { allows, useWorkspace, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";

const STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
};

/**
 * The top bar's Jobs (spec story 46): a button with the number of jobs still active, and
 * the Workspace's background jobs in a Popover: each one's status, progress and log.
 * Members cancel the jobs they started; owners cancel anyone's.
 */
export function JobsButton({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  return workspace.data ? <JobsPopover workspace={workspace.data} /> : null;
}

function JobsPopover({ workspace }: { workspace: Workspace }) {
  const jobs = useJobs(workspace.id);
  const me = useMe();
  const cancel = useCancelJob(workspace.id);
  const running = jobs.data?.items.filter(isActive).length ?? 0;

  function canCancel(job: Job): boolean {
    return (
      isActive(job) &&
      (allows(workspace, "job.cancel_any") ||
        (allows(workspace, "job.cancel_own") &&
          me.data != null &&
          job.created_by === me.data.id))
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={`Jobs, ${running} running`}
        >
          <Loader aria-hidden />
          {running > 0 && (
            <span
              aria-hidden
              className="absolute -top-px -right-px grid min-w-4 place-items-center rounded-full bg-accent-solid px-1 text-2xs leading-4 font-semibold text-on-accent"
            >
              {running}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="max-h-[70vh] w-[min(24rem,calc(100vw-2rem))] overflow-y-auto"
        aria-label="Background jobs"
        role="region"
      >
        {jobs.isPending && <p className="text-sand-11">Loading…</p>}
        {jobs.isError && (
          <p role="alert">Could not load the jobs: {jobs.error.message}</p>
        )}
        {jobs.isSuccess && jobs.data.items.length === 0 && (
          <p className="text-sand-11">No background jobs yet.</p>
        )}
        {jobs.isSuccess && jobs.data.items.length > 0 && (
          <ul className="flex flex-col">
            {jobs.data.items.map((job) => (
              <li
                key={job.id}
                aria-label={job.title}
                className="flex flex-col gap-1.5 border-b border-sand-4 py-2 first:pt-0 last:border-b-0 last:pb-0"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{job.title}</span>
                  <span className="text-sm text-sand-11">
                    {STATUS_LABELS[job.status]}
                  </span>
                </div>
                <progress
                  max={100}
                  value={job.progress}
                  aria-label={`${job.title} progress`}
                  className="w-full accent-accent-solid"
                >
                  {job.progress}%
                </progress>
                {job.error && (
                  <p role="alert" className="text-sm text-red-11">
                    {job.error}
                  </p>
                )}
                {job.log && (
                  <details>
                    <summary className="cursor-pointer text-sm text-sand-11">
                      Log
                    </summary>
                    <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-sand-3 p-2 text-xs whitespace-pre-wrap">
                      {job.log}
                    </pre>
                  </details>
                )}
                {canCancel(job) && (
                  <Button
                    size="sm"
                    className="self-start"
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate(job.id)}
                  >
                    Cancel job
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {cancel.isError && (
          <p role="alert" className="text-red-11">
            {cancel.error.message}
          </p>
        )}
      </PopoverContent>
    </Popover>
  );
}
