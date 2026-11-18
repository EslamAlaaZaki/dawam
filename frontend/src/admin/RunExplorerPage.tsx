"use client";

import { X } from "lucide-react";
import {
  Fragment,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";

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
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Loading } from "../shell/Loading";
import {
  EmptyState,
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
} from "./parts";

const STATUSES = ["running", "completed", "tool_limit", "cancelled", "failed"];

function statusVariant(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed" || status === "ok") return "success";
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

/** A duration as "420 ms", "8.4 s" or "1m 5s". */
function duration(ms: number): string {
  if (ms >= 60000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** A token count, shortened from a thousand up ("24.2k"). */
function count(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  return n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
}

/** How long the run took; a run still going has no time yet. */
function responseTime(run: RunSummary): string {
  return run.status === "running" ? "—" : duration(run.duration_ms);
}

/** The summary row, worked out from the runs loaded into the table. */
function summarise(runs: RunSummary[]) {
  const finished = runs.filter((run) => run.status !== "running");
  const priced = runs.filter((run) => run.cost);
  return {
    runs: runs.length,
    incomplete: runs.filter((run) => ["failed", "tool_limit", "cancelled"].includes(run.status))
      .length,
    average: finished.length
      ? Math.round(finished.reduce((sum, run) => sum + run.duration_ms, 0) / finished.length)
      : null,
    cost: priced.reduce((sum, run) => sum + Number(run.cost!.amount), 0),
    currency: priced[0]?.cost?.currency ?? "USD",
    up: runs.reduce((sum, run) => sum + run.thumbs_up, 0),
    down: runs.reduce((sum, run) => sum + run.thumbs_down, 0),
  };
}

/** The admin's view of the AI's runs; a tab per kind of run, Assistant runs first. */
export function RunExplorerPage() {
  return (
    <section className="flex flex-col gap-6">
      <PageHead
        title="AI runs"
        description="What the assistant did for each question: model calls, tool calls, tokens, cost and errors."
      />
      <Tabs defaultValue="assistant" className="gap-4">
        <TabsList aria-label="Run source" className="px-0">
          <TabsTrigger value="assistant">Assistant runs</TabsTrigger>
        </TabsList>
        <TabsContent value="assistant">
          <AssistantRuns />
        </TabsContent>
      </Tabs>
    </section>
  );
}

/** The assistant's runs across all Workspaces; a row opens the run's step timeline in a side sheet. */
function AssistantRuns() {
  const [filters, setFilters] = useState<RunFilters>({});
  const [selected, setSelected] = useState<string | null>(null);
  const opener = useRef<string | null>(null);
  const rows = useRef(new Map<string, HTMLTableRowElement>());
  const runs = useAssistantRuns(filters);
  const workspaces = useAdminWorkspaces();
  const members = useUsers({});

  function open(id: string) {
    opener.current = id;
    setSelected(id);
  }

  function onRowKey(event: KeyboardEvent<HTMLTableRowElement>, id: string) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open(id);
    }
  }

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
    <div className="flex flex-col gap-4">
      {runs.data && <SummaryRow runs={runs.data} />}
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
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Started</TableHead>
                <TableHead scope="col">Workspace · member</TableHead>
                <TableHead scope="col">Status</TableHead>
                <TableHead scope="col">Model</TableHead>
                <TableHead scope="col" className="text-right">Tool calls</TableHead>
                <TableHead scope="col" className="text-right">Response time</TableHead>
                <TableHead scope="col" className="text-right">Tokens in / out</TableHead>
                <TableHead scope="col" className="text-right">Cost</TableHead>
                <TableHead scope="col">Feedback</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data.map((run) => (
                <TableRow
                  key={run.id}
                  ref={(node) => {
                    if (node) rows.current.set(run.id, node);
                    else rows.current.delete(run.id);
                  }}
                  aria-label={`Run ${run.id}`}
                  aria-selected={selected === run.id}
                  tabIndex={0}
                  className="cursor-pointer aria-selected:bg-primary-subtle"
                  onClick={() => open(run.id)}
                  onKeyDown={(event) => onRowKey(event, run.id)}
                >
                  <TableCell className="text-muted-foreground">{when(run.started_at)}</TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">{run.workspace_name ?? "—"}</span>
                      <span className="text-xs text-subtle-foreground">
                        {run.member_email ?? "—"}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(run.status)} dot>
                      {run.status}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {run.model_name ? (
                      <Badge className="font-mono">{run.model_name}</Badge>
                    ) : (
                      <span className="text-subtle-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell numeric>{run.tool_call_count}</TableCell>
                  <TableCell numeric>{responseTime(run)}</TableCell>
                  <TableCell numeric mono>
                    {count(run.input_tokens)} / {count(run.output_tokens)}
                  </TableCell>
                  <TableCell numeric mono>
                    <Cost cost={run.cost} />
                  </TableCell>
                  <TableCell>
                    {run.thumbs_up || run.thumbs_down ? (
                      <span className="inline-flex gap-2.5">
                        <span className="text-success">{run.thumbs_up} up</span>
                        <span className="text-danger">{run.thumbs_down} down</span>
                      </span>
                    ) : (
                      <span className="text-subtle-foreground">none</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {runs.data.length === 0 && <EmptyState>No runs match.</EmptyState>}
        </TableCard>
      )}
      <Sheet open={selected !== null} onOpenChange={(isOpen) => !isOpen && setSelected(null)}>
        <SheetContent
          side="right"
          className="max-w-2xl bg-surface-raised"
          onCloseAutoFocus={(event) => {
            const row = opener.current ? rows.current.get(opener.current) : undefined;
            if (row) {
              event.preventDefault();
              row.focus();
            }
          }}
        >
          {selected && <RunSheet runId={selected} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Cost({ cost }: { cost: RunSummary["cost"] }) {
  return cost ? (
    <>{money(cost)}</>
  ) : (
    <span className="text-subtle-foreground">unpriced</span>
  );
}

/** Runs, runs that did not finish, average response time, cost and feedback for the loaded runs. */
function SummaryRow({ runs }: { runs: RunSummary[] }) {
  const sum = summarise(runs);
  const stats: { label: string; value: ReactNode; hint: string }[] = [
    { label: "Runs", value: sum.runs, hint: "in this list" },
    {
      label: "Did not complete",
      value: <span className={sum.incomplete ? "text-danger" : undefined}>{sum.incomplete}</span>,
      hint: "failed, tool limit or cancelled",
    },
    {
      label: "Avg response time",
      value: sum.average === null ? "—" : duration(sum.average),
      hint: "finished runs",
    },
    { label: "Cost", value: `${sum.cost.toFixed(2)} ${sum.currency}`, hint: "priced runs only" },
    {
      label: "Feedback",
      value: (
        <>
          <span className="text-success">{sum.up}</span> /{" "}
          <span className="text-danger">{sum.down}</span>
        </>
      ),
      hint: "thumbs up / down",
    },
  ];
  return (
    <dl
      aria-label="Summary"
      className="grid grid-cols-2 overflow-hidden rounded-pill border border-border bg-surface-raised sm:grid-cols-5"
    >
      {stats.map((stat) => (
        <div
          key={stat.label}
          className="flex min-w-0 flex-col gap-0.5 border-s border-b border-border px-3.5 py-3 first:border-s-0 max-sm:odd:border-s-0 sm:border-b-0"
        >
          <dt className="text-xs text-subtle-foreground">{stat.label}</dt>
          <dd className="truncate text-2xl font-semibold tabular-nums">{stat.value}</dd>
          <p className="text-xs text-subtle-foreground">{stat.hint}</p>
        </div>
      ))}
    </dl>
  );
}

/** The sheet's content: the run's header, details, step timeline and member feedback. */
function RunSheet({ runId }: { runId: string }) {
  const run = useAssistantRun(runId);
  const status = run.data?.run.status;
  return (
    <>
      <header className="flex items-start gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 flex-1">
          <SheetTitle className="flex items-center gap-2 text-lg font-semibold">
            Assistant run
            {status && (
              <Badge variant={statusVariant(status)} dot>
                {status}
              </Badge>
            )}
          </SheetTitle>
          <SheetDescription className="mt-0.5 font-mono text-xs break-all text-subtle-foreground">
            {runId}
          </SheetDescription>
        </div>
        <SheetClose asChild>
          <Button type="button" variant="ghost" size="icon" aria-label="Close">
            <X aria-hidden />
          </Button>
        </SheetClose>
      </header>
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-5 py-4">
        {run.isPending ? (
          <Loading />
        ) : run.isError ? (
          <ErrorText>Could not load the run: {run.error.message}</ErrorText>
        ) : (
          <RunDetailView detail={run.data} />
        )}
      </div>
    </>
  );
}

function RunDetailView({ detail }: { detail: RunDetail }) {
  const { run } = detail;
  const total = Math.max(
    1,
    detail.timeline.reduce((sum, step) => sum + (step.duration_ms ?? 0), 0),
  );
  const details: [string, ReactNode][] = [
    ["Workspace", run.workspace_name ?? "—"],
    ["Member", run.member_email ?? "—"],
    ["Started", when(run.started_at)],
    ["Model", <span className="font-mono">{run.model_name ?? "—"}</span>],
    ["Prompt version", <span className="font-mono">{run.prompt_version ?? "—"}</span>],
    ["Duration", responseTime(run)],
    [
      "Tokens in / out",
      <span className="font-mono">
        {count(run.input_tokens)} / {count(run.output_tokens)}
      </span>,
    ],
    [
      "Cost",
      <span className="font-mono">
        <Cost cost={run.cost} />
      </span>,
    ],
    ["Tool calls", run.tool_call_count],
  ];
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-5 gap-y-3 text-sm sm:grid-cols-3">
        {details.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-subtle-foreground">{label}</dt>
            <dd className="break-words tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <section aria-label="Run timeline" className="flex flex-col gap-2.5">
        <h3 className="text-sm font-semibold">Timeline</h3>
        <ol className="flex flex-col">
          {detail.timeline.map((step, index) => (
            <Step
              key={index}
              step={step}
              share={((step.duration_ms ?? 0) / total) * 100}
              last={index === detail.timeline.length - 1}
            />
          ))}
        </ol>
        <p className="text-xs text-subtle-foreground">
          The model&apos;s calls in one run are stored as one combined step.
        </p>
      </section>
      <section aria-label="Member feedback" className="flex flex-col gap-2.5">
        <h3 className="text-sm font-semibold">Member feedback</h3>
        {detail.feedback.length === 0 ? (
          <p className="text-sm text-muted-foreground">No feedback yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {detail.feedback.map((f, index) => (
              <li
                key={index}
                className="flex flex-col gap-1 rounded-md border border-border px-3 py-2.5 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={f.rating === "up" ? "success" : "danger"}>
                    {f.rating === "up" ? "Thumbs up" : "Thumbs down"}
                  </Badge>
                  <span>{f.member_email ?? "A member"}</span>
                </div>
                {f.comment && <p>{f.comment}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

const MARKERS: Record<string, string> = { model: "M", tool: "T", error: "!" };

function Step({ step, share, last }: { step: RunStep; share: number; last: boolean }) {
  const kind =
    step.kind === "model" ? "Model calls" : step.kind === "tool" ? "Tool call" : "Error";
  const marker =
    step.kind === "error"
      ? "border-danger bg-danger-subtle text-danger"
      : step.kind === "model"
        ? "border-primary text-primary"
        : "border-border text-muted-foreground";
  const hasArguments = step.arguments && Object.keys(step.arguments).length > 0;
  return (
    <li
      aria-label={`${kind} ${step.name}`}
      className={`relative grid grid-cols-[1.25rem_minmax(0,1fr)] gap-2.5 ${last ? "" : "pb-4"}`}
    >
      {!last && <span aria-hidden className="absolute start-2 top-5 bottom-0 w-px bg-border" />}
      <span
        aria-hidden
        className={`grid size-5 place-items-center rounded-full border bg-surface-raised font-mono text-2xs ${marker}`}
      >
        {MARKERS[step.kind] ?? "•"}
      </span>
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
          <span className="font-medium">{kind}</span>
          <Badge className="font-mono">{step.name}</Badge>
          <Badge variant={statusVariant(step.status)} dot>
            {step.status}
          </Badge>
        </div>
        {step.kind !== "error" && (
          <>
            <div className="flex flex-wrap gap-x-3.5 gap-y-1 text-xs text-subtle-foreground tabular-nums">
              {step.duration_ms !== null && <span>{duration(step.duration_ms)}</span>}
              {step.input_tokens !== null && (
                <span className="font-mono">
                  {step.input_tokens} in / {step.output_tokens} out tokens
                </span>
              )}
              {step.kind === "model" && (
                <span className="font-mono">
                  <Cost cost={step.cost} />
                </span>
              )}
            </div>
            {step.duration_ms !== null && (
              <div aria-hidden className="h-1 max-w-xs overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full ${step.kind === "model" ? "bg-primary" : "bg-subtle-foreground"}`}
                  style={{ width: `${Math.max(2, share)}%` }}
                />
              </div>
            )}
          </>
        )}
        {(step.prompt || step.output) && (
          <Collapsible summary="Question and answer" open>
            {step.prompt && <Block label="Question (redacted)" text={step.prompt} />}
            {step.output && <Block label="Answer (redacted)" text={step.output} />}
          </Collapsible>
        )}
        {(hasArguments || step.result) && (
          <Collapsible summary="Arguments and result">
            {hasArguments && (
              <Block label="Arguments" text={JSON.stringify(step.arguments, null, 2)} mono />
            )}
            {step.result && (
              <Block label="Result" text={JSON.stringify(step.result, null, 2)} mono />
            )}
          </Collapsible>
        )}
        {step.error_message && <p className="text-sm text-danger">{step.error_message}</p>}
      </div>
    </li>
  );
}

function Collapsible({
  summary,
  open = false,
  children,
}: {
  summary: string;
  open?: boolean;
  children: ReactNode;
}) {
  return (
    <details open={open} className="text-sm">
      <summary className="w-max cursor-pointer text-xs text-muted-foreground">{summary}</summary>
      {children}
    </details>
  );
}

/** A redacted text with its [PERSON]-style placeholders marked. */
function Redacted({ text }: { text: string }) {
  return text.split(/(\[[A-Z_]+\])/).map((part, index) =>
    /^\[[A-Z_]+\]$/.test(part) ? (
      <span key={index} className="rounded-sm bg-muted px-0.5 text-subtle-foreground">
        {part}
      </span>
    ) : (
      <Fragment key={index}>{part}</Fragment>
    ),
  );
}

function Block({ label, text, mono = false }: { label: string; text: string; mono?: boolean }) {
  return (
    <div className="mt-2">
      <div className="text-xs text-subtle-foreground">{label}</div>
      <pre
        className={`mt-1 max-h-60 overflow-auto rounded-md border border-border bg-surface p-2.5 break-words whitespace-pre-wrap ${mono ? "font-mono text-mono" : "font-sans text-sm"}`}
      >
        {mono ? text : <Redacted text={text} />}
      </pre>
    </div>
  );
}
