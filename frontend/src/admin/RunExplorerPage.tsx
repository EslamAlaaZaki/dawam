"use client";

import { useState, type FormEvent } from "react";

import {
  useAssistantRun,
  useAssistantRuns,
  type RunDetail,
  type RunFilters,
  type RunStep,
  type RunSummary,
} from "../api/adminRuns";
import { useAdminWorkspaces } from "../api/adminWorkspaces";
import { useUsers } from "../api/users";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  EmptyState,
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  h3Classes,
  labelClasses,
} from "./parts";

const STATUSES = ["running", "completed", "tool_limit", "cancelled", "failed"];

function statusVariant(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "tool_limit" || status === "cancelled") return "warning";
  return "neutral";
}

/** Midnight at the start of a `YYYY-MM-DD` day in the admin's time zone, `days` later. */
function startOfDay(day: string, days = 0): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year!, month! - 1, date! + days).toISOString();
}

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

function money(cost: RunSummary["cost"]): string {
  return cost ? `${Number(cost.amount).toFixed(6)} ${cost.currency}` : "unpriced";
}

/** The assistant's runs across all Workspaces, with each run's step timeline. */
export function RunExplorerPage() {
  const [filters, setFilters] = useState<RunFilters>({});
  const [selected, setSelected] = useState<string | null>(null);
  const runs = useAssistantRuns(filters);
  const workspaces = useAdminWorkspaces();
  const members = useUsers({});

  function onFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const workspace = String(form.get("workspace_id") ?? "");
    const member = String(form.get("member_id") ?? "");
    const status = String(form.get("status") ?? "");
    const from = String(form.get("from") ?? "");
    const to = String(form.get("to") ?? "");
    setFilters({
      ...(workspace && { workspace_id: workspace }),
      ...(member && { member_id: member }),
      ...(status && { status }),
      ...(from && { started_from: startOfDay(from) }),
      ...(to && { started_to: startOfDay(to, 1) }), // the whole of the "To" day
    });
  }

  return (
    <section className="flex flex-col gap-6">
      <PageHead
        title="Assistant runs"
        description="What the assistant did for each question: model calls, tool calls, tokens, cost and errors."
      />
      <form
        className="flex flex-wrap items-end gap-3"
        aria-label="Filter runs"
        onSubmit={onFilter}
      >
        <label className={labelClasses}>
          Workspace
          <NativeSelect name="workspace_id" defaultValue="">
            <option value="">Any Workspace</option>
            {workspaces.data?.pages
              .flatMap((page) => page.items)
              .map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Member
          <NativeSelect name="member_id" defaultValue="">
            <option value="">Any member</option>
            {members.data?.pages
              .flatMap((page) => page.items)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Status
          <NativeSelect name="status" defaultValue="">
            <option value="">Any status</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          From
          <Input name="from" type="date" />
        </label>
        <label className={labelClasses}>
          To
          <Input name="to" type="date" />
        </label>
        <Button type="submit" size="lg">
          Filter
        </Button>
      </form>
      {runs.isPending ? (
        <Loading />
      ) : runs.isError ? (
        <ErrorText>Could not load the runs: {runs.error.message}</ErrorText>
      ) : (
        <>
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Started</TableHead>
                  <TableHead scope="col">Workspace</TableHead>
                  <TableHead scope="col">Member</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead scope="col">Tokens in / out</TableHead>
                  <TableHead scope="col">Cost</TableHead>
                  <TableHead scope="col">Feedback</TableHead>
                  <TableHead scope="col">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.data.map((run) => (
                  <TableRow key={run.id} aria-label={`Run ${run.id}`}>
                    <TableCell className="text-muted-foreground">
                      {when(run.started_at)}
                    </TableCell>
                    <TableCell>{run.workspace_name ?? "—"}</TableCell>
                    <TableCell>{run.member_email ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(run.status)}>{run.status}</Badge>
                    </TableCell>
                    <TableCell numeric>
                      {run.input_tokens} / {run.output_tokens}
                    </TableCell>
                    <TableCell mono className="text-muted-foreground">
                      {money(run.cost)}
                    </TableCell>
                    <TableCell>
                      {run.thumbs_up} up, {run.thumbs_down} down
                    </TableCell>
                    <TableCell>
                      <Button
                        size="sm"
                        aria-label={`Open run ${run.id}`}
                        aria-pressed={selected === run.id}
                        onClick={() => setSelected(run.id)}
                      >
                        Timeline
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          {runs.data.length === 0 && <EmptyState>No runs match.</EmptyState>}
        </>
      )}
      <RunTimeline runId={selected} />
    </section>
  );
}

function RunTimeline({ runId }: { runId: string | null }) {
  const run = useAssistantRun(runId);
  if (runId === null) return null;
  if (run.isPending) return <Loading />;
  if (run.isError) {
    return <ErrorText>Could not load the run: {run.error.message}</ErrorText>;
  }
  return <RunDetailView detail={run.data} />;
}

function RunDetailView({ detail }: { detail: RunDetail }) {
  const { run } = detail;
  return (
    <section
      aria-label="Run timeline"
      className="flex flex-col gap-4 rounded-pill border border-border bg-surface-raised p-5"
    >
      <h3 className={h3Classes}>Run timeline</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Prompt version</dt>
        <dd className="font-mono">{run.prompt_version ?? "—"}</dd>
        <dt className="text-muted-foreground">Model</dt>
        <dd className="font-mono">{run.model_name ?? "—"}</dd>
        <dt className="text-muted-foreground">Duration</dt>
        <dd>{run.duration_ms} ms</dd>
        <dt className="text-muted-foreground">Cost</dt>
        <dd className="font-mono">{money(run.cost)}</dd>
      </dl>
      <ol className="flex flex-col gap-3">
        {detail.timeline.map((step, index) => (
          <Step key={index} step={step} />
        ))}
      </ol>
      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-semibold">Member feedback</h4>
        {detail.feedback.length === 0 ? (
          <p className="text-sm text-muted-foreground">No feedback yet.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {detail.feedback.map((f, index) => (
              <li key={index}>
                {f.member_email ?? "A member"}: thumbs {f.rating}
                {f.comment ? ` — ${f.comment}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Step({ step }: { step: RunStep }) {
  const kind =
    step.kind === "model" ? "Model calls" : step.kind === "tool" ? "Tool call" : "Error";
  return (
    <li
      aria-label={`${kind} ${step.name}`}
      className="flex flex-col gap-1.5 border-s-2 border-border ps-3"
    >
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">{kind}</span>
        <Badge variant="neutral" className="font-mono">
          {step.name}
        </Badge>
        <Badge variant={statusVariant(step.status)}>{step.status}</Badge>
        {step.duration_ms !== null && (
          <span className="text-muted-foreground">{step.duration_ms} ms</span>
        )}
        {step.input_tokens !== null && (
          <span className="text-muted-foreground">
            {step.input_tokens} in / {step.output_tokens} out tokens
          </span>
        )}
        {step.kind === "model" && (
          <span className="font-mono text-muted-foreground">{money(step.cost)}</span>
        )}
      </div>
      {step.prompt && <Block label="Prompt (redacted)" text={step.prompt} />}
      {step.output && <Block label="Answer (redacted)" text={step.output} />}
      {step.arguments && Object.keys(step.arguments).length > 0 && (
        <Block label="Arguments" text={JSON.stringify(step.arguments, null, 2)} mono />
      )}
      {step.result && (
        <Block label="Result" text={JSON.stringify(step.result, null, 2)} mono />
      )}
      {step.error_message && <ErrorText>{step.error_message}</ErrorText>}
    </li>
  );
}

function Block({ label, text, mono = false }: { label: string; text: string; mono?: boolean }) {
  return (
    <div className="text-sm">
      <div className="text-xs text-muted-foreground">{label}</div>
      <pre
        className={`whitespace-pre-wrap break-words ${mono ? "font-mono text-mono" : "font-sans"}`}
      >
        {text}
      </pre>
    </div>
  );
}
