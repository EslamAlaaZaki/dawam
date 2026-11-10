"use client";

import {
  Ban,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Inbox,
  ListChecks,
  LoaderCircle,
  PauseCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import {
  isActive,
  useCancelJob,
  useJobs,
  type Job,
  type JobStatus,
} from "../api/jobs";
import { useMe } from "../api/queries";
import { allows, useWorkspace, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { relativeTime } from "../lib/relativeTime";
import { Loading } from "../shell/Loading";
import { ErrorText } from "./parts";

type BadgeVariant = "neutral" | "info" | "warning" | "success" | "danger";

const STATUS: Record<JobStatus, { label: string; variant: BadgeVariant; icon: LucideIcon }> = {
  queued: { label: "Queued", variant: "neutral", icon: CircleDashed },
  running: { label: "Running", variant: "info", icon: LoaderCircle },
  paused: { label: "Waiting for your answer", variant: "warning", icon: PauseCircle },
  succeeded: { label: "Succeeded", variant: "success", icon: CheckCircle2 },
  failed: { label: "Failed", variant: "danger", icon: XCircle },
  cancelled: { label: "Cancelled", variant: "neutral", icon: Ban },
};

const BAR: Partial<Record<JobStatus, string>> = {
  running: "bg-info",
  paused: "bg-warning",
  queued: "bg-border-strong",
};

function StatusBadge({ status }: { status: JobStatus }) {
  const { label, variant, icon: Icon } = STATUS[status];
  return (
    <Badge variant={variant} className="shrink-0">
      <Icon aria-hidden />
      {label}
    </Badge>
  );
}

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
  const items = jobs.data?.items ?? [];
  const active = items.filter(isActive);
  const recent = items.filter((job) => !isActive(job));
  const running = items.filter(isActive).length;

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
          <ListChecks aria-hidden />
          {running > 0 && (
            <span
              aria-hidden
              className="absolute -top-px -end-px grid min-w-4 place-items-center rounded-full bg-primary px-1 text-2xs leading-4 font-semibold text-primary-foreground tabular-nums ring-2 ring-background"
            >
              {running}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="flex max-h-[70vh] w-[min(26rem,calc(100vw-2rem))] flex-col p-0"
        aria-label="Background jobs"
        role="region"
      >
        <div className="flex items-baseline gap-2 border-b border-border px-3 py-2">
          <h3 className="text-base font-semibold">Background jobs</h3>
          <span className="ms-auto text-xs text-subtle-foreground tabular-nums">
            {active.length} active
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          {jobs.isPending && <Loading className="p-3" />}
          {jobs.isError && (
            <div className="px-3 py-2">
              <ErrorText>Could not load the jobs: {jobs.error.message}</ErrorText>
            </div>
          )}
          {jobs.isSuccess && items.length === 0 && (
            <div className="flex flex-col items-center gap-1 px-3 py-8 text-center">
              <Inbox aria-hidden className="mb-1 size-6 text-subtle-foreground" />
              <p className="text-muted-foreground">No background jobs yet.</p>
            </div>
          )}
          {items.length > 0 && (
            // One list, so a job keeps its element when it moves from Active to Recent.
            <ul className="flex flex-col">
              {[
                ...(active.length > 0 ? [<GroupLabel key="active" label="Active" />] : []),
                ...active.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    cancellable={canCancel(job)}
                    cancelPending={cancel.isPending}
                    onCancel={() => cancel.mutate(job.id)}
                  />
                )),
                ...(recent.length > 0 ? [<GroupLabel key="recent" label="Recent" />] : []),
                ...recent.map((job) => (
                  <JobRow key={job.id} job={job} cancellable={false} cancelPending={false} />
                )),
              ]}
            </ul>
          )}
          {cancel.isError && (
            <div className="px-3 py-2">
              <ErrorText>{cancel.error.message}</ErrorText>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function GroupLabel({ label }: { label: string }) {
  return (
    <li role="presentation">
      <h4 className="px-3 pt-2 pb-1 text-xs font-medium text-subtle-foreground">{label}</h4>
    </li>
  );
}

function JobRow({
  job,
  cancellable,
  cancelPending,
  onCancel,
}: {
  job: Job;
  cancellable: boolean;
  cancelPending: boolean;
  onCancel?: () => void;
}) {
  const [logOpen, setLogOpen] = useState(false);
  const live = isActive(job);
  return (
    <li
      aria-label={job.title}
      className="flex flex-col gap-1.5 border-b border-border px-3 py-2 last:border-b-0"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium" title={job.title}>
            {job.title}
          </p>
          <p className="truncate text-xs text-subtle-foreground">
            <span className="font-mono">{job.type}</span> · {relativeTime(job.created_at)}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>
      {live && (
        <div className="flex items-center gap-2">
          <progress
            max={100}
            value={job.progress}
            aria-label={`${job.title} progress`}
            className="sr-only"
          >
            {job.progress}%
          </progress>
          <div aria-hidden className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full rounded-full ${BAR[job.status] ?? "bg-border-strong"}`}
              style={{ width: `${job.progress}%` }}
            />
          </div>
          <span aria-hidden className="text-xs text-muted-foreground tabular-nums">
            {job.progress}%
          </span>
        </div>
      )}
      {job.error && (
        <div className="flex items-start gap-1.5 rounded-md bg-danger-subtle px-2 py-1.5 text-sm text-danger-strong">
          <XCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>{job.error}</span>
        </div>
      )}
      {(job.log || cancellable) && (
        <div className="flex items-center justify-between gap-2">
          {job.log ? (
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={logOpen}
              onClick={() => setLogOpen((open) => !open)}
            >
              <ChevronRight
                aria-hidden
                className={`transition-transform ${logOpen ? "rotate-90" : ""}`}
              />
              Log
            </Button>
          ) : (
            <span />
          )}
          {cancellable && (
            <Button
              variant="ghost"
              size="sm"
              aria-label="Cancel job"
              className="text-danger-strong hover:text-danger-strong"
              disabled={cancelPending}
              onClick={onCancel}
            >
              Cancel
            </Button>
          )}
        </div>
      )}
      {job.log && (
        <pre
          hidden={!logOpen}
          className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre-wrap"
        >
          {job.log}
        </pre>
      )}
    </li>
  );
}
