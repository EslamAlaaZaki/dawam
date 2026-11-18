"use client";

import { useState, type FormEvent, type ReactNode } from "react";

import {
  useAiJob,
  useAiJobs,
  type AiJob,
  type AiJobDetail,
  type AiJobFilters,
} from "../api/adminAiJobs";
import { useAdminWorkspaces } from "../api/adminWorkspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Sheet, SheetContent } from "../components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import { EmptyState, ErrorText, NativeSelect, TableCard, labelClasses } from "./parts";
import {
  Cost,
  Details,
  SheetHeader,
  SummaryStats,
  count,
  duration,
  rowClasses,
  startOfDay,
  statusVariant,
  useRowSheet,
  when,
  type Stat,
} from "./runExplorerParts";

const FEATURES: [string, string][] = [
  ["catalog_suggestions", "Catalog suggestions"],
  ["pii_scan", "PII scan"],
  ["relationships", "Relationships"],
  ["kpi_suggestions", "KPI suggestions"],
  ["file_indexing", "File indexing"],
  ["search", "Document search"],
  ["unknown", "Unknown"],
];
const STATUSES = ["completed", "partial", "failed", "running"];

function featureName(feature: string): string {
  return FEATURES.find(([value]) => value === feature)?.[1] ?? feature;
}

/** Jobs, model calls, failed calls, tokens and cost for the loaded rows. */
function summarise(jobs: AiJob[]): Stat[] {
  const priced = jobs.filter((job) => job.cost);
  const currencies = new Set(priced.map((job) => job.cost!.currency));
  const cost = priced.reduce((sum, job) => sum + Number(job.cost!.amount), 0);
  const failed = jobs.reduce((sum, job) => sum + job.failed_calls, 0);
  return [
    { label: "Jobs", value: jobs.length, hint: "jobs and search days" },
    { label: "Model calls", value: jobs.reduce((sum, job) => sum + job.calls, 0), hint: "in this list" },
    {
      label: "Failed calls",
      value: <span className={failed ? "text-danger" : undefined}>{failed}</span>,
      hint: "model calls that failed",
    },
    {
      label: "Tokens",
      value: (
        <span className="font-mono">
          {count(jobs.reduce((sum, job) => sum + job.input_tokens, 0))} /{" "}
          {count(jobs.reduce((sum, job) => sum + job.output_tokens, 0))}
        </span>
      ),
      hint: "in / out",
    },
    {
      label: "Cost",
      value:
        currencies.size > 1 ? (
          "mixed"
        ) : (
          <Cost
            cost={priced.length ? { amount: String(cost), currency: [...currencies][0]! } : null}
          />
        ),
      hint: priced.length < jobs.length ? "priced jobs only" : "all jobs priced",
    },
  ];
}

/** Model calls made outside the assistant; a row opens the job's calls per model in a side sheet. */
export function JobsAndSearch() {
  const [filters, setFilters] = useState<AiJobFilters>({});
  const sheet = useRowSheet();
  const jobs = useAiJobs(filters);
  const workspaces = useAdminWorkspaces();

  function onFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const workspace = String(form.get("workspace_id") ?? "");
    const feature = String(form.get("feature") ?? "");
    const status = String(form.get("status") ?? "");
    const from = String(form.get("from") ?? "");
    const to = String(form.get("to") ?? "");
    setFilters({
      ...(workspace && { workspace_id: workspace }),
      ...(feature && { feature }),
      ...(status && { status }),
      ...(from && { started_from: startOfDay(from) }),
      ...(to && { started_to: startOfDay(to, 1) }), // the whole of the "To" day
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Model calls made outside the assistant. One row per job; document searches are one row per
        Workspace per day.
      </p>
      {jobs.data && <SummaryStats stats={summarise(jobs.data)} />}
      <form className="flex flex-wrap items-end gap-3" aria-label="Filter jobs" onSubmit={onFilter}>
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
          Feature
          <NativeSelect name="feature" defaultValue="">
            <option value="">Any feature</option>
            {FEATURES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
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
      {jobs.isPending ? (
        <Loading />
      ) : jobs.isError ? (
        <ErrorText>Could not load the jobs: {jobs.error.message}</ErrorText>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Started</TableHead>
                <TableHead scope="col">Feature · target</TableHead>
                <TableHead scope="col">Workspace · started by</TableHead>
                <TableHead scope="col">Status</TableHead>
                <TableHead scope="col" className="text-right">Calls</TableHead>
                <TableHead scope="col" className="text-right">Duration</TableHead>
                <TableHead scope="col" className="text-right">Tokens in / out</TableHead>
                <TableHead scope="col" className="text-right">Cost</TableHead>
                <TableHead scope="col">Models</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {jobs.data.map((job) => (
                <TableRow
                  key={job.key}
                  ref={sheet.rowRef(job.key)}
                  aria-label={`Job ${job.key}`}
                  data-state={sheet.selected === job.key ? "selected" : undefined}
                  tabIndex={0}
                  className={rowClasses}
                  onClick={() => sheet.open(job.key)}
                  onKeyDown={(event) => sheet.onRowKey(event, job.key)}
                >
                  <TableCell className="text-muted-foreground">{when(job.started_at)}</TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">{featureName(job.feature)}</span>
                      {job.target && (
                        <span className="font-mono text-xs text-subtle-foreground">
                          {job.target}
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium">{job.workspace_name ?? "—"}</span>
                      <span className="text-xs text-subtle-foreground">{job.started_by}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(job.status)} dot>
                      {job.status}
                    </Badge>
                  </TableCell>
                  <TableCell numeric>
                    {job.calls}
                    {job.failed_calls > 0 && (
                      <span className="block text-xs text-danger">{job.failed_calls} failed</span>
                    )}
                  </TableCell>
                  <TableCell numeric>
                    {job.status === "running" ? "—" : duration(job.duration_ms)}
                  </TableCell>
                  <TableCell numeric mono>
                    {count(job.input_tokens)} / {count(job.output_tokens)}
                  </TableCell>
                  <TableCell numeric mono>
                    <Cost cost={job.cost} />
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {job.models.map((model) => (
                        <Badge key={model} className="font-mono">
                          {model}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {jobs.data.length === 0 && <EmptyState>No jobs match.</EmptyState>}
        </TableCard>
      )}
      <Sheet open={sheet.selected !== null} onOpenChange={(isOpen) => !isOpen && sheet.close()}>
        <SheetContent
          side="right"
          className="max-w-2xl bg-surface-raised"
          onCloseAutoFocus={sheet.onCloseAutoFocus}
        >
          {sheet.last && <JobSheet jobKey={sheet.last} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

/** The sheet's content: the job's header, details, calls per model, token share and errors. */
function JobSheet({ jobKey }: { jobKey: string }) {
  const job = useAiJob(jobKey);
  const summary = job.data?.job;
  return (
    <>
      <SheetHeader
        title={summary ? featureName(summary.feature) : "Job"}
        badge={
          summary && (
            <Badge variant={statusVariant(summary.status)} dot>
              {summary.status}
            </Badge>
          )
        }
        id={jobKey}
      />
      <div className="flex flex-1 flex-col gap-5 overflow-y-auto px-5 py-4">
        {job.isPending ? (
          <Loading />
        ) : job.isError ? (
          <ErrorText>Could not load the job: {job.error.message}</ErrorText>
        ) : (
          <JobDetailView detail={job.data} />
        )}
      </div>
    </>
  );
}

function JobDetailView({ detail }: { detail: AiJobDetail }) {
  const { job } = detail;
  const isSearch = job.feature === "search";
  const totalTokens = Math.max(
    1,
    detail.models.reduce((sum, m) => sum + m.input_tokens + m.output_tokens, 0),
  );
  const details: [string, ReactNode][] = [
    ["Target", job.target ? <span className="font-mono">{job.target}</span> : "—"],
    ["Workspace", job.workspace_name ?? "—"],
    ["Started by", job.started_by],
    ["Started", when(job.started_at)],
    ["Duration", job.status === "running" ? "—" : duration(job.duration_ms)],
    [
      "Calls",
      job.failed_calls ? (
        <>
          {job.calls} <span className="text-danger">({job.failed_calls} failed)</span>
        </>
      ) : (
        job.calls
      ),
    ],
    [
      "Tokens in / out",
      <span className="font-mono">
        {count(job.input_tokens)} / {count(job.output_tokens)}
      </span>,
    ],
    [
      "Cost",
      <span className="font-mono">
        <Cost cost={job.cost} />
      </span>,
    ],
  ];
  return (
    <>
      <Details items={details} />
      <section aria-label="Calls by model" className="flex flex-col gap-2.5">
        <h3 className="text-sm font-semibold">Calls by model</h3>
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Role</TableHead>
                <TableHead scope="col">Model</TableHead>
                <TableHead scope="col" className="text-right">Calls</TableHead>
                <TableHead scope="col" className="text-right">Failed</TableHead>
                <TableHead scope="col" className="text-right">Avg latency</TableHead>
                <TableHead scope="col" className="text-right">Tokens in / out</TableHead>
                <TableHead scope="col" className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.models.map((m) => (
                <TableRow key={`${m.role}/${m.model_name}`}>
                  <TableCell>
                    <Badge>{m.role}</Badge>
                  </TableCell>
                  <TableCell mono>{m.model_name}</TableCell>
                  <TableCell numeric>{m.calls}</TableCell>
                  <TableCell numeric>
                    <span className={m.failed_calls ? "text-danger" : undefined}>
                      {m.failed_calls}
                    </span>
                  </TableCell>
                  <TableCell numeric>
                    {m.avg_latency_ms === null ? "—" : duration(m.avg_latency_ms)}
                  </TableCell>
                  <TableCell numeric mono>
                    {count(m.input_tokens)} / {count(m.output_tokens)}
                  </TableCell>
                  <TableCell numeric mono>
                    <Cost cost={m.cost} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      </section>
      <section aria-label="Token share" className="flex flex-col gap-2.5">
        <h3 className="text-sm font-semibold">Token share</h3>
        <ul className="flex flex-col gap-2">
          {detail.models.map((m) => {
            const share = ((m.input_tokens + m.output_tokens) / totalTokens) * 100;
            return (
              <li key={`${m.role}/${m.model_name}`} className="flex flex-col gap-1 text-xs">
                <div className="flex justify-between gap-2 text-subtle-foreground">
                  <span className="font-mono">{m.model_name}</span>
                  <span className="font-mono">{Math.round(share)}%</span>
                </div>
                <div aria-hidden className="h-1 overflow-hidden rounded-full bg-muted">
                  <div className="h-full bg-primary" style={{ width: `${Math.max(2, share)}%` }} />
                </div>
              </li>
            );
          })}
        </ul>
      </section>
      <section aria-label="Errors" className="flex flex-col gap-2.5">
        <h3 className="text-sm font-semibold">Errors</h3>
        {detail.failed_calls.length === 0 ? (
          <p className="text-sm text-muted-foreground">No failed calls.</p>
        ) : (
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Time</TableHead>
                  <TableHead scope="col">Error</TableHead>
                  <TableHead scope="col">Model</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.failed_calls.map((f, index) => (
                  <TableRow key={index}>
                    <TableCell className="text-muted-foreground">{when(f.at)}</TableCell>
                    <TableCell>
                      <Badge variant="danger" className="font-mono">
                        {f.error_code ?? "unknown"}
                      </Badge>
                    </TableCell>
                    <TableCell mono>{f.model_name}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
        )}
      </section>
      <p className="text-xs text-subtle-foreground">
        Prompts and outputs are not stored for these calls.
        {isSearch && " Searches are grouped per Workspace per day (UTC)."}
      </p>
    </>
  );
}
