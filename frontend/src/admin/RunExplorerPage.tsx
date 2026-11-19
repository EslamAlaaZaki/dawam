"use client";

import {
  Fragment,
  useState,
  type FormEvent,
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
import { useAiJobs } from "../api/adminAiJobs";
import { useAdminWorkspaces } from "../api/adminWorkspaces";
import { useUsers } from "../api/users";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Sheet,
  SheetContent,
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
import { JobsAndSearch } from "./RunExplorerJobs";
import {
  Cost,
  Details,
  SheetHeader,
  SummaryStats,
  count,
  duration,
  money,
  rowClasses,
  startOfDay,
  statusVariant,
  useRowSheet,
  when,
} from "./runExplorerParts";

const STATUSES = ["running", "completed", "tool_limit", "cancelled", "failed"];

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
    currency: new Set(priced.map((run) => run.cost!.currency)).size > 1 ? null : (priced[0]?.cost?.currency ?? "USD"),
    up: runs.reduce((sum, run) => sum + run.thumbs_up, 0),
    down: runs.reduce((sum, run) => sum + run.thumbs_down, 0),
  };
}

/** The admin's view of the AI's runs; a tab per kind of run, Assistant runs first. */
export function RunExplorerPage() {
  // The tabs' counts, kept in view while the other tab is open (the same lists as each tab's first load).
  const runCount = useAssistantRuns({}).data?.length;
  const jobCount = useAiJobs({}).data?.length;
  return (
    <section className="flex flex-col gap-6">
      <PageHead
        title="AI runs"
        description="What the assistant did for each question: model calls, tool calls, tokens, cost and errors."
      />
      <Tabs defaultValue="assistant" className="gap-4">
        <TabsList aria-label="Run source" className="px-0">
          <TabsTrigger value="assistant">Assistant runs{runCount !== undefined && <TabCount n={runCount} />}</TabsTrigger>
          <TabsTrigger value="jobs">Jobs &amp; search{jobCount !== undefined && <TabCount n={jobCount} />}</TabsTrigger>
        </TabsList>
        <TabsContent value="assistant">
          <AssistantRuns />
        </TabsContent>
        <TabsContent value="jobs">
          <JobsAndSearch />
        </TabsContent>
      </Tabs>
    </section>
  );
}

function TabCount({ n }: { n: number }) {
  return <span className="ms-1.5 font-mono text-xs text-subtle-foreground">{n}</span>;
}

/** The assistant's runs across all Workspaces; a row opens the run's step timeline in a side sheet. */
function AssistantRuns() {
  const [filters, setFilters] = useState<RunFilters>({});
  const sheet = useRowSheet();
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
                  ref={sheet.rowRef(run.id)}
                  aria-label={`Run ${run.id}`}
                  data-state={sheet.selected === run.id ? "selected" : undefined}
                  tabIndex={0}
                  className={rowClasses}
                  onClick={() => sheet.open(run.id)}
                  onKeyDown={(event) => sheet.onRowKey(event, run.id)}
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
                        <span className="text-success">{run.thumbs_up} up</span>{" "}
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
      <Sheet open={sheet.selected !== null} onOpenChange={(isOpen) => !isOpen && sheet.close()}>
        <SheetContent
          side="right"
          className="max-w-2xl bg-surface-raised"
          onCloseAutoFocus={sheet.onCloseAutoFocus}
        >
          {sheet.last && <RunSheet runId={sheet.last} />}
        </SheetContent>
      </Sheet>
    </div>
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
    { label: "Cost", value: sum.currency ? money({ amount: String(sum.cost), currency: sum.currency }) : "mixed currencies", hint: "priced runs only" },
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
  return <SummaryStats stats={stats} />;
}

/** The sheet's content: the run's header, details, step timeline and member feedback. */
function RunSheet({ runId }: { runId: string }) {
  const run = useAssistantRun(runId);
  const status = run.data?.run.status;
  return (
    <>
      <SheetHeader
        title="Assistant run"
        badge={
          status && (
            <Badge variant={statusVariant(status)} dot>
              {status}
            </Badge>
          )
        }
        id={runId}
      />
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
      <Details items={details} />
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
          <SentTag mode={step.tool_result?.sent_mode ?? null} />
        </div>
        {(
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
        {(hasArguments || step.result || step.tool_result) && (
          <Collapsible summary="Arguments and result">
            {hasArguments && (
              <Block label="Arguments" text={JSON.stringify(step.arguments, null, 2)} mono />
            )}
            {!step.tool_result?.stored && step.result && (
              <Block label="Result" text={JSON.stringify(step.result, null, 2)} mono />
            )}
            {step.tool_result && <StoredResult result={step.tool_result} />}
          </Collapsible>
        )}
        {step.error_message && <p className="text-sm text-danger">{step.error_message}</p>}
      </div>
    </li>
  );
}

const JSON_CUT = 1200;

/** Whether the model got this result in full on the next follow-ups, or only a digest. */
function SentTag({ mode }: { mode: string | null }) {
  if (mode === "full") {
    return (
      <Badge variant="accent" title="Sent back in full with the next 2 questions">
        Sent in full
      </Badge>
    );
  }
  if (mode === "digest_over_cap") {
    return <Badge>Sent as digest · over 4,000 tokens</Badge>;
  }
  return null;
}

type StoredResultData = NonNullable<RunStep["tool_result"]>;

/** A tool step's stored result: a table when tabular, else cut JSON or text. */
function StoredResult({ result }: { result: StoredResultData }) {
  const [showAll, setShowAll] = useState(false);
  if (!result.stored) {
    return (
      <p className="mt-2 text-xs text-subtle-foreground">
        No result stored: this run is older than full tool results.
      </p>
    );
  }
  const size = (
    <span className="font-mono tabular-nums">
      {result.token_size?.toLocaleString("en-US") ?? 0} tokens
    </span>
  );
  if (result.columns && result.rows) {
    return (
      <div className="mt-2">
        <div className="text-xs text-subtle-foreground">Result (redacted)</div>
        <div className="mt-1 max-h-60 overflow-auto rounded-md border border-border bg-surface">
          <table className="w-full border-collapse font-mono text-mono">
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c} className="border-b border-border px-2 py-1 text-start font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((v, j) => (
                    <td
                      key={j}
                      className={`px-2 py-1 ${typeof v === "number" ? "text-end tabular-nums" : ""}`}
                    >
                      {v === null || v === undefined
                        ? ""
                        : typeof v === "object"
                          ? JSON.stringify(v)
                          : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-subtle-foreground">
          <span className="tabular-nums">
            Showing {result.rows.length.toLocaleString("en-US")} of{" "}
            {(result.row_count ?? result.rows.length).toLocaleString("en-US")} rows
          </span>
          {size}
        </p>
      </div>
    );
  }
  const full =
    result.text ?? (result.data === null ? "" : JSON.stringify(result.data, null, 2)) ?? "";
  const cut = !showAll && full.length > JSON_CUT;
  return (
    <div>
      <Block label="Result (redacted)" text={cut ? `${full.slice(0, JSON_CUT)}…` : full} mono />
      <p className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-subtle-foreground">
        {full.length > JSON_CUT && (
          <Button variant="ghost" size="sm" onClick={() => setShowAll(!showAll)}>
            {showAll ? "Show less" : "Show all"}
          </Button>
        )}
        {size}
      </p>
    </div>
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
