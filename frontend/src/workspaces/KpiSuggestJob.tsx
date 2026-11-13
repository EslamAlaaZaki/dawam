// The Suggest KPIs button of a Source System's KPIs page and the status bar of its job
// (spec story 75): the AI suggests KPIs for that system only, in the background.
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../api/client";
import type { Kpi } from "../api/kpis";
import {
  isRunning,
  useKpiSuggestionJob,
  useStartKpiSuggestions,
  type KpiSuggestionJob,
} from "../api/kpiSuggestions";
import { useSourceSystems } from "../api/systems";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { cn } from "../lib/utils";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * For owners and editors: the button (secondary, beside Add KPI) and, under the header, a
 * status bar while the job runs and once it ends: KPIs suggested, none to suggest, or failed.
 * `kpis` is every KPI of the system in every review state; `onSuggested` runs when a job
 * this page watched ends with new suggestions (the page shows the Suggested tab).
 */
export function KpiSuggestJob({
  workspaceId,
  systemId,
  kpis,
  disabled,
  onSuggested,
}: {
  workspaceId: string;
  systemId: string;
  kpis: readonly Kpi[];
  /** E.g. an archived Workspace: the button shows but cannot start a job. */
  disabled?: boolean;
  onSuggested: () => void;
}) {
  const queryClient = useQueryClient();
  const job = useKpiSuggestionJob(workspaceId, systemId, true);
  const start = useStartKpiSuggestions(workspaceId, systemId);
  const systems = useSourceSystems(workspaceId);
  const name = systems.data?.find((s) => s.id === systemId)?.name ?? "this Source System";
  const current = job.data ?? null;
  const running = isRunning(current);
  // The job whose end this page reports: one seen running here (started here, or already
  // running when the page opened). A job that ended while nobody watched shows nothing.
  const [watched, setWatched] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  if (current && running && watched !== current.job_id) {
    setWatched(current.job_id);
  }
  const ended = current && !running && current.job_id === watched ? current : null;
  const endedId = ended?.job_id;
  const created = ended?.created ?? 0;

  useEffect(() => {
    if (endedId === undefined) {
      return;
    }
    void queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "kpis"] });
    if (created > 0) {
      onSuggested();
    }
    // Once per ended job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endedId]);

  const rejected = kpis.filter((k) => k.review_state === "rejected").length;

  function onStart() {
    start.mutate(undefined, {
      onSuccess: (started) => setWatched(started.job_id),
      onError: (error) =>
        toast.error(
          error instanceof ApiError && error.code === "kpi_suggestions_running"
            ? "KPIs are already being suggested for this Source System."
            : `Could not start: ${error.message}`,
        ),
    });
  }

  return (
    <>
      <Button
        variant="secondary"
        onClick={onStart}
        disabled={disabled || running || start.isPending}
      >
        {running || start.isPending ? "Suggesting…" : "Suggest KPIs"}
      </Button>
      {running ? (
        <StatusBar tone="running">
          <Loader2 aria-hidden className="mt-0.5 size-4 shrink-0 text-info motion-safe:animate-spin" />
          <Message title={`Suggesting KPIs for ${name}…`}>
            The AI reads the Source Schema, profiles and documents, and the{" "}
            {plural(kpis.length, "KPI", "KPIs")} already here ({rejected} rejected), so it only
            suggests different ones. You can leave this page; the job keeps running.
          </Message>
        </StatusBar>
      ) : ended && ended.job_id !== dismissed ? (
        <Outcome
          job={ended}
          name={name}
          total={kpis.length}
          onDismiss={() => setDismissed(ended.job_id)}
        />
      ) : null}
    </>
  );
}

function Outcome({
  job,
  name,
  total,
  onDismiss,
}: {
  job: KpiSuggestionJob;
  name: string;
  total: number;
  onDismiss: () => void;
}) {
  const created = job.created ?? 0;
  const dropped = job.dropped ?? 0;
  const droppedLine =
    dropped > 0
      ? ` ${plural(dropped, "more was", "more were")} dropped: based on tables outside ${name}.`
      : "";
  const close = (
    <Button
      variant="ghost"
      size="icon"
      className="ml-auto shrink-0"
      aria-label="Dismiss"
      onClick={onDismiss}
    >
      <X aria-hidden />
    </Button>
  );
  if (job.status !== "succeeded") {
    return (
      <StatusBar tone="failed">
        <Message title="Suggesting KPIs failed">
          {job.error || "The job ended before it finished."}
        </Message>
        {close}
      </StatusBar>
    );
  }
  if (created > 0) {
    return (
      <StatusBar tone="done">
        <Message title={`${plural(created, "new KPI", "new KPIs")} suggested`}>
          They are in the Suggested tab for you to accept or reject.{droppedLine}
        </Message>
        {close}
      </StatusBar>
    );
  }
  return (
    <StatusBar tone="none">
      <Message title="No new KPIs to suggest">
        The AI found nothing that differs from the {plural(total, "KPI", "KPIs")} already here,
        including rejected ones.{droppedLine}
      </Message>
      {close}
    </StatusBar>
  );
}

const TONES = {
  running: "border-info bg-info-subtle",
  done: "border-success bg-success-subtle",
  none: "border-border bg-surface",
  failed: "border-danger bg-danger-subtle",
} as const;

/** A full-width row under the page header (`order-last` in its wrapping flex row). */
function StatusBar({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "order-last flex basis-full items-start gap-3 rounded-lg border px-3.5 py-2.5 text-sm",
        TONES[tone],
      )}
    >
      {children}
    </div>
  );
}

function Message({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="font-medium text-foreground">{title}</div>
      <div className="text-muted-foreground">{children}</div>
    </div>
  );
}
