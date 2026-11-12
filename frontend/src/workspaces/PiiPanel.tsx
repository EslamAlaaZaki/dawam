"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { isActive, useJobs } from "../api/jobs";
import { useMembers } from "../api/members";
import {
  CATEGORY_LABELS,
  useDecidePiiColumn,
  usePiiColumns,
  useStartPiiScan,
  type PiiCategory,
  type PiiColumn,
  type PiiRuleMatch,
  type PiiTab,
} from "../api/pii";
import { useStartProfiling } from "../api/profiling";
import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import {
  AcceptRejectedPiiDialog,
  AddPiiColumnDialog,
  CategoryBadge,
  CategorySelect,
  RemovePiiDialog,
} from "./PiiDialogs";
import { ScanDialog } from "./PiiScanDialog";
import { Confidence, initials } from "./RelationshipReview";
import { EmptyState, ErrorText, TableCard, linkButtonClasses, monoClasses } from "./parts";

const TABS: readonly { tab: PiiTab; label: string; empty: string }[] = [
  { tab: "pii", label: "PII", empty: "No PII columns yet." },
  {
    tab: "suggested",
    label: "Suggested",
    empty: "Nothing to review. Scan the values to look for more.",
  },
  { tab: "accepted", label: "Accepted", empty: "No accepted columns." },
  { tab: "rejected", label: "Rejected", empty: "No rejected columns." },
];

/** The source filter of the PII tab: a column matches if any of its rules has it. */
type SourceFilter = "all" | "name" | "value" | "manual";

const SOURCE_FILTERS: { source: SourceFilter; label: string }[] = [
  { source: "all", label: "All" },
  { source: "name", label: "Name scan" },
  { source: "value", label: "Value scan" },
  { source: "manual", label: "Manual" },
];

const matchesSource = (column: PiiColumn, source: SourceFilter) =>
  source === "all" ||
  (source === "manual" ? column.manual : column.rules.some((r) => r.source === source));

const SOURCE_LABELS: Record<PiiRuleMatch["source"], string> = {
  name: "Name",
  value: "Values",
};

const DECISION_VERBS = {
  accepted: "Accepted by",
  added: "Added by",
  rejected: "Rejected by",
  removed: "Removed by",
} as const;

const gone = (column: PiiColumn) => column.source_status !== "present";

const nameOf = (names: ReadonlyMap<string, string>, id: string | null) =>
  (id && names.get(id)) || "a member";

/** The column as schema.table over the column name, in mono, with its state tags. */
function ColumnCell({ column, note }: { column: PiiColumn; note?: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className={cn(monoClasses, "text-xs text-subtle-foreground")}>
        {column.db_schema}.{column.table}
      </span>
      <span className={cn(monoClasses, "break-all", gone(column) && "line-through")}>
        {column.column}
      </span>
      {(gone(column) || column.hashed_mapping) && (
        <span className="flex flex-wrap gap-1">
          {gone(column) && <Badge>Removed from source</Badge>}
          {column.hashed_mapping && <Badge variant="info">Hashed in staging</Badge>}
        </span>
      )}
      {note}
    </div>
  );
}

/**
 * Every rule that matched the column, most confident first: where it comes from, the rule
 * (mono tag), its confidence and, unless `compact`, its evidence (full text in a tooltip).
 */
function MatchedRules({ column, compact = false }: { column: PiiColumn; compact?: boolean }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {column.rules.map((rule) => (
        <li key={rule.id} className="flex flex-col gap-0.5">
          <span className="flex flex-wrap items-center gap-2">
            <span className="w-12 text-xs text-muted-foreground">
              {SOURCE_LABELS[rule.source]}
            </span>
            <Badge variant="info" className={monoClasses}>
              {rule.rule}
            </Badge>
            <Confidence ratio={rule.confidence} />
          </span>
          {!compact && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0} className="max-w-80 truncate text-xs text-muted-foreground">
                  {rule.evidence}
                </span>
              </TooltipTrigger>
              <TooltipContent>{rule.evidence}</TooltipContent>
            </Tooltip>
          )}
        </li>
      ))}
      {column.manual && (
        <li className="flex items-center gap-2">
          <span className="w-12 text-xs text-muted-foreground">Manual</span>
          <span className="text-xs text-muted-foreground">Added by hand</span>
        </li>
      )}
    </ul>
  );
}

/** Who decided and when, with an initials avatar: "Accepted by Ada · 1/6/2026". */
function DecidedBy({
  verb,
  by,
  at,
  names,
}: {
  verb: string;
  by: string | null;
  at: string | null;
  names: ReadonlyMap<string, string>;
}) {
  const name = nameOf(names, by);
  return (
    <span className="inline-flex items-center gap-2 text-muted-foreground">
      <span
        aria-hidden
        className="grid size-6 shrink-0 place-items-center rounded-full bg-muted text-xs font-medium"
      >
        {initials(name)}
      </span>
      <span>
        {verb} {name}
        {at && ` · ${new Date(at).toLocaleDateString()}`}
      </span>
    </span>
  );
}

function Decided({ column, names }: { column: PiiColumn; names: ReadonlyMap<string, string> }) {
  if (!column.decision) {
    return <span className="text-subtle-foreground">—</span>;
  }
  return (
    <DecidedBy
      verb={DECISION_VERBS[column.decision]}
      by={column.decided_by}
      at={column.decided_at}
      names={names}
    />
  );
}

/**
 * A Suggested row's category dropdown, preset to the most confident rule's category, with
 * an amber note when the column's rules suggest different categories.
 */
function SuggestedCategory({
  column,
  value,
  onChange,
}: {
  column: PiiColumn;
  value: PiiCategory;
  onChange: (category: PiiCategory) => void;
}) {
  const top = column.rules[0]?.category;
  const others = [...new Set(column.rules.map((r) => r.category))].filter((c) => c !== top);
  return (
    <div className="flex flex-col gap-1">
      <CategorySelect value={value} onChange={onChange} label={`Category of ${column.column}`} />
      {others.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="inline-flex items-center gap-1 text-xs text-warning-strong">
              <TriangleAlert aria-hidden className="size-3" />
              Rules disagree
            </span>
          </TooltipTrigger>
          <TooltipContent>
            Other rules suggest {others.map((c) => CATEGORY_LABELS[c]).join(", ")}
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}

/**
 * A Source System's PII folder (spec §6.12): the PII columns, and for owners and editors
 * the Suggested, Accepted and Rejected tabs, one row per column with every rule that matched
 * it. Accept and Reject move a whole column; owners and editors add PII by hand and remove
 * it. Viewers read the PII tab only. Changes are hidden in an archived Workspace.
 */
export function PiiPanel({ workspace, system }: { workspace: Workspace; system: SourceSystem }) {
  const canReview = workspace.status !== "archived" && allows(workspace, "pii.review");
  const canProfileRight =
    workspace.status !== "archived" && allows(workspace, "source_system.profile");
  const [tab, setTab] = useState<PiiTab>("pii");
  const shownTab: PiiTab = canReview ? tab : "pii";
  const [source, setSource] = useState<SourceFilter>("all");
  const [scanOpen, setScanOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<PiiColumn | null>(null);
  const [accepting, setAccepting] = useState<PiiColumn | null>(null);
  const [categories, setCategories] = useState<Record<string, PiiCategory>>({});
  const list = usePiiColumns(workspace.id, system.id, shownTab);
  const piiTab = usePiiColumns(workspace.id, system.id, "pii", canReview && adding);
  const schema = useSourceSchema(workspace.id, system.id, canReview && adding);
  const scan = useStartPiiScan(workspace.id, system.id);
  const decide = useDecidePiiColumn(workspace.id, system.id);
  const profile = useStartProfiling(workspace.id, system.id);
  const members = useMembers(workspace.id);
  const names = useMemo(
    () => new Map((members.data ?? []).map((m) => [m.user_id, m.display_name])),
    [members.data],
  );
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = scan.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: ["workspace", workspace.id, "systems", system.id],
      });
    }
  }, [finished, queryClient, workspace.id, system.id]);

  const page = list.data;
  const counts = page?.counts;
  const canProfile = canProfileRight && (page?.can_profile ?? false);
  const items = (page?.items ?? []).filter(
    (c) => shownTab !== "pii" || matchesSource(c, source),
  );
  const sourceCount = (s: SourceFilter) =>
    (page?.items ?? []).filter((c) => matchesSource(c, s)).length;
  const current = TABS.find((t) => t.tab === shownTab)!;

  const reject = (column: PiiColumn) =>
    decide.mutate(
      { columnId: column.id, action: "reject" },
      {
        onSuccess: () => {
          if (canProfile && column.table_profiled_at) {
            toast.success(`Rejected ${column.column}.`, {
              action: {
                label: "Profile again",
                onClick: () =>
                  profile.mutate([column.table_id], {
                    onSuccess: () =>
                      toast.success(
                        `Profiling ${column.table} again. Follow it under Background jobs.`,
                      ),
                    onError: (error) => toast.error(error.message),
                  }),
              },
            });
          } else {
            toast.success(`Rejected ${column.column}.`);
          }
        },
      },
    );

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-3">
      <h3 id="folder-title" className="sr-only">
        PII
      </h3>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {canReview && (
          <Tabs value={tab} onValueChange={(value) => setTab(value as PiiTab)}>
            <TabsList className="px-0">
              {TABS.map((t) => (
                <TabsTrigger key={t.tab} value={t.tab} className="text-sm">
                  {t.label}
                  {counts && (
                    <span className="ms-1 font-normal text-subtle-foreground tabular-nums">
                      {counts[t.tab]}
                    </span>
                  )}
                  {t.tab === "suggested" && (counts?.suggested ?? 0) > 0 && (
                    <span aria-hidden className="ms-1 size-1.5 rounded-full bg-warning" />
                  )}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        )}
        {canReview && (
          <div className="ms-auto flex flex-wrap gap-2">
            <Button
              type="button"
              variant="secondary"
              disabled={scan.isPending}
              onClick={() => setScanOpen(true)}
            >
              Scan values
            </Button>
            {tab === "pii" && (
              <Button type="button" variant="primary" onClick={() => setAdding(true)}>
                <Plus aria-hidden />
                Add PII column
              </Button>
            )}
          </div>
        )}
      </div>
      {!canReview && (
        <p className="m-0 text-sm text-muted-foreground">
          Read only. Owners and Editors review and change PII.
        </p>
      )}
      {canReview && scanOpen && (
        <ScanDialog
          workspace={workspace}
          system={system}
          pending={scan.isPending}
          error={scan.error}
          onClose={() => setScanOpen(false)}
          onStart={(tableIds) =>
            scan.mutate(tableIds, {
              onSuccess: () => {
                setScanOpen(false);
                toast.success(
                  "Scan started. Follow it under Background jobs; new findings appear here when it finishes.",
                );
              },
            })
          }
        />
      )}
      {canReview && (
        <>
          <AddPiiColumnDialog
            open={adding}
            onOpenChange={setAdding}
            workspaceId={workspace.id}
            systemId={system.id}
            tables={schema.data?.tables ?? []}
            taken={new Set((piiTab.data?.items ?? []).map((c) => c.id))}
          />
          <RemovePiiDialog
            target={removing}
            onClose={() => setRemoving(null)}
            workspaceId={workspace.id}
            systemId={system.id}
            canProfile={canProfile}
            decidedBy={
              removing?.decision
                ? `${DECISION_VERBS[removing.decision]} ${nameOf(names, removing.decided_by)}`
                : ""
            }
          />
          <AcceptRejectedPiiDialog
            target={accepting}
            onClose={() => setAccepting(null)}
            workspaceId={workspace.id}
            systemId={system.id}
          />
        </>
      )}
      {canReview && shownTab === "pii" && (counts?.suggested ?? 0) > 0 && (
        <p className="m-0 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-warning-subtle px-3 py-2 text-sm text-warning-strong">
          <TriangleAlert aria-hidden className="size-4" />
          {counts!.suggested} suggested{" "}
          {counts!.suggested === 1 ? "column is" : "columns are"} also protected until
          someone reviews them.
          <button type="button" className={linkButtonClasses} onClick={() => setTab("suggested")}>
            Review suggested
          </button>
        </p>
      )}
      {shownTab === "pii" && page && page.items.length > 0 && (
        <div role="group" aria-label="Filter by source" className="flex flex-wrap gap-2">
          {SOURCE_FILTERS.map((f) => (
            <Button
              key={f.source}
              type="button"
              size="sm"
              variant="secondary"
              className={
                source === f.source ? "border-primary bg-primary-subtle text-foreground" : undefined
              }
              aria-pressed={source === f.source}
              onClick={() => setSource(f.source)}
            >
              {f.label}
              <span className="tabular-nums">{sourceCount(f.source)}</span>
            </Button>
          ))}
        </div>
      )}
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {list.isPending && <Loading label="Loading PII columns…" className="p-0" />}
      {list.isError && <ErrorText>Could not load the PII columns: {list.error.message}</ErrorText>}
      {list.isSuccess && items.length === 0 && <EmptyState>{current.empty}</EmptyState>}
      {list.isSuccess && items.length > 0 && (
        <TableCard>
          <Table aria-label={`${current.label} columns`}>
            <TableHeader>
              <TableRow>
                <TableHead>Column</TableHead>
                {shownTab === "pii" && <TableHead>Type</TableHead>}
                <TableHead>Category</TableHead>
                <TableHead>Matched rules</TableHead>
                {shownTab !== "suggested" && <TableHead>Decided</TableHead>}
                {(canReview || shownTab !== "pii") && (
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((column) => (
                <TableRow
                  key={column.id}
                  className={shownTab === "rejected" ? "text-muted-foreground" : undefined}
                >
                  <TableCell className="whitespace-normal">
                    <ColumnCell
                      column={column}
                      note={
                        shownTab === "suggested" &&
                        column.rejected_before_by !== null && (
                          <span className="text-xs text-muted-foreground">
                            Rejected before by {nameOf(names, column.rejected_before_by)}
                            {column.rejected_before_at &&
                              ` · ${new Date(column.rejected_before_at).toLocaleDateString()}`}
                          </span>
                        )
                      }
                    />
                  </TableCell>
                  {shownTab === "pii" && (
                    <TableCell className={cn(monoClasses, "text-muted-foreground")}>
                      {column.data_type ?? "—"}
                    </TableCell>
                  )}
                  <TableCell className="whitespace-normal">
                    {shownTab === "suggested" ? (
                      <SuggestedCategory
                        column={column}
                        value={categories[column.id] ?? column.category ?? "sensitive"}
                        onChange={(category) =>
                          setCategories((last) => ({ ...last, [column.id]: category }))
                        }
                      />
                    ) : (
                      <CategoryBadge category={column.category} />
                    )}
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    <MatchedRules column={column} compact={shownTab === "pii"} />
                  </TableCell>
                  {shownTab !== "suggested" && (
                    <TableCell className="whitespace-normal">
                      <Decided column={column} names={names} />
                    </TableCell>
                  )}
                  {(canReview || shownTab !== "pii") && (
                    <TableCell>
                      <RowActions
                        tab={shownTab}
                        column={column}
                        pending={decide.isPending}
                        onAccept={() =>
                          decide.mutate(
                            {
                              columnId: column.id,
                              action: "accept",
                              category: categories[column.id] ?? column.category ?? "sensitive",
                            },
                            { onSuccess: () => toast.success(`Accepted ${column.column} as PII.`) },
                          )
                        }
                        onReject={() => reject(column)}
                        onRemove={() => setRemoving(column)}
                        onAcceptRejected={() => setAccepting(column)}
                      />
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
    </section>
  );
}

/** Accept / Reject for a suggested column; the badge and delete or Accept otherwise. */
function RowActions({
  tab,
  column,
  pending,
  onAccept,
  onReject,
  onRemove,
  onAcceptRejected,
}: {
  tab: PiiTab;
  column: PiiColumn;
  pending: boolean;
  onAccept: () => void;
  onReject: () => void;
  onRemove: () => void;
  onAcceptRejected: () => void;
}) {
  const removeButton = !gone(column) && (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={`Remove PII from ${column.column}`}
      onClick={onRemove}
    >
      <Trash2 aria-hidden />
    </Button>
  );
  if (tab === "suggested") {
    return (
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="text-success-strong"
          disabled={pending}
          onClick={onAccept}
        >
          Accept
        </Button>
        <Button type="button" size="sm" variant="ghost" disabled={pending} onClick={onReject}>
          Reject
        </Button>
      </div>
    );
  }
  if (tab === "rejected") {
    return (
      <div className="flex items-center gap-2">
        <Badge>Rejected</Badge>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="text-success-strong"
          onClick={onAcceptRejected}
        >
          Accept
        </Button>
      </div>
    );
  }
  if (tab === "accepted") {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="mapped">Accepted</Badge>
        {removeButton}
      </div>
    );
  }
  return removeButton || null;
}
