"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Network, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { isActive, useJobs } from "../api/jobs";
import { useMembers } from "../api/members";
import {
  relationshipsKey,
  useDecideRelationship,
  useRelationshipsByStatus,
  useStartInference,
  type Relationship,
  type RelationshipStatus,
} from "../api/relationships";
import { useSourceSchema } from "../api/snapshots";
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
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import {
  AcceptRejectedDialog,
  AddRelationshipDialog,
  DeleteRelationshipDialog,
  type DeleteTarget,
} from "./RelationshipDialogs";
import { RelationshipsTable } from "./RelationshipsTable";
import { RelationshipVisual, relationshipEnds, type Endpoint } from "./RelationshipVisual";
import { relationshipEdges, type ErEdge } from "./erGraph";
import {
  EmptyState,
  ErrorText,
  TableCard,
  h5Classes,
  monoClasses,
} from "./parts";

/** "current" lists the relationships in effect; the others the inferred ones by status. */
type View = "current" | RelationshipStatus;

const TABS: { status: View; label: string }[] = [
  { status: "current", label: "Current" },
  { status: "suggested", label: "Suggested" },
  { status: "accepted", label: "Accepted" },
  { status: "rejected", label: "Rejected" },
];

type Signals = Record<
  string,
  { score?: number; detail?: string; objects?: string[]; ratio?: number }
>;

const percent = (ratio: number) => `${Math.round(ratio * 100)}%`;

/** The evidence signals as short chips: names and ratios only, never a data value. */
function signalChips(relationship: Relationship): string[] {
  const signals = (relationship.evidence.signals ?? {}) as Signals;
  const chips: string[] = [];
  for (const key of ["name", "type", "unique"]) {
    if ((signals[key]?.score ?? 0) > 0) {
      chips.push(key);
    }
  }
  if (signals.join) {
    chips.push(`join: ${(signals.join.objects ?? []).join(", ")}`);
  }
  if (signals.overlap?.ratio != null) {
    chips.push(`overlap ${percent(signals.overlap.ratio)}`);
  }
  return chips;
}

const columnName = (c: Relationship["from_column"]) =>
  `${c.db_schema}.${c.table}.${c.column}`;

/**
 * Find relationships (spec story 60): starts the inference job and refreshes the lists when
 * it finishes. Needs the profiling right and an active Workspace; otherwise renders nothing.
 * The Source schema page shows it in its header, next to Profile all tables.
 */
export function FindRelationships({
  workspace,
  systemId,
  onStarted,
}: {
  workspace: Workspace;
  systemId: string;
  onStarted?: () => void;
}) {
  const start = useStartInference(workspace.id, systemId);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = start.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: relationshipsKey(workspace.id, systemId),
      });
    }
  }, [finished, queryClient, workspace.id, systemId]);

  const canFind =
    workspace.status !== "archived" && allows(workspace, "source_system.profile");
  const running = start.isPending || (started != null && isActive(started));
  if (!canFind) {
    return null;
  }
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        type="button"
        disabled={running}
        onClick={() =>
          start.mutate(undefined, {
            onSuccess: () => {
              toast.success(
                "Looking for relationships. Follow it under Background jobs.",
              );
              onStarted?.();
            },
          })
        }
      >
        <Network aria-hidden />
        {running ? "Finding relationships…" : "Find relationships"}
      </Button>
      {start.isError && <ErrorText>{start.error.message}</ErrorText>}
    </div>
  );
}

/** A column as `schema.table.column`, in mono. */
function ColumnRef({ column }: { column: Relationship["from_column"] }) {
  return <span className={`${monoClasses} break-all`}>{columnName(column)}</span>;
}

/** Confidence as a short bar and its percentage. */
export function Confidence({ ratio }: { ratio: number | null }) {
  if (ratio == null) {
    return <span className="text-subtle-foreground">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums">
      <span aria-hidden className="h-1 w-16 overflow-hidden rounded-full bg-muted">
        <span
          className={cn("block h-full", ratio < 0.7 ? "bg-warning" : "bg-primary")}
          style={{ width: percent(ratio) }}
        />
      </span>
      <span>{percent(ratio)}</span>
    </span>
  );
}

/**
 * Accept and Reject for a suggested relationship; the decision's badge otherwise, with a
 * delete button on an accepted one when `onDelete` is given.
 */
function Decision({
  relationship,
  canDecide,
  decide,
  onDelete,
  onAcceptRejected,
}: {
  relationship: Relationship;
  canDecide: boolean;
  decide: ReturnType<typeof useDecideRelationship>;
  onDelete?: (relationship: Relationship) => void;
  onAcceptRejected?: (relationship: Relationship) => void;
}) {
  if (relationship.status !== "suggested") {
    return relationship.status === "accepted" ? (
      <div className="flex items-center gap-2">
        <Badge variant="mapped">Accepted</Badge>
        {onDelete && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Delete relationship"
            onClick={() => onDelete(relationship)}
          >
            <Trash2 aria-hidden />
          </Button>
        )}
      </div>
    ) : (
      <div className="flex items-center gap-2">
        <Badge>Rejected</Badge>
        {onAcceptRejected && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="text-success-strong"
            onClick={() => onAcceptRejected(relationship)}
          >
            Accept
          </Button>
        )}
      </div>
    );
  }
  if (!canDecide) {
    return null;
  }
  return (
    <div className="flex gap-2">
      <Button
        type="button"
        size="sm"
        variant="secondary"
        className="text-success-strong"
        disabled={decide.isPending}
        onClick={() => decide.mutate({ id: relationship.id, decision: "accept" })}
      >
        Accept
      </Button>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        disabled={decide.isPending}
        onClick={() => decide.mutate({ id: relationship.id, decision: "reject" })}
      >
        Reject
      </Button>
    </div>
  );
}

/** The kind filter of the Current list. */
type KindFilter = "all" | ErEdge["kind"];

const KIND_FILTERS: { kind: KindFilter; label: string }[] = [
  { kind: "all", label: "All" },
  { kind: "declared", label: "Declared" },
  { kind: "inferred", label: "Inferred" },
  { kind: "manual", label: "Manual" },
];

export const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join("");

/** Who decided and when, with an initials avatar: "Accepted by Ada · 1/6/2026". */
function DecidedBy({
  verb,
  relationship,
  names,
}: {
  verb: string;
  relationship: Relationship;
  names: ReadonlyMap<string, string>;
}) {
  const name = (relationship.decided_by && names.get(relationship.decided_by)) || "a member";
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
        {relationship.decided_at &&
          ` · ${new Date(relationship.decided_at).toLocaleDateString()}`}
      </span>
    </span>
  );
}

/** Evidence chips, or "Added by hand" for a manual relationship. */
function Evidence({ relationship }: { relationship: Relationship }) {
  if (relationship.origin === "manual") {
    return <span className="text-muted-foreground">Added by hand</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {signalChips(relationship).map((chip) => (
        <Badge key={chip} variant="info">
          {chip}
        </Badge>
      ))}
    </div>
  );
}

const originLabel = (origin: Relationship["origin"]) =>
  origin === "manual" ? "Manually added" : origin === "routine" ? "Routine JOIN" : "Rules";

/**
 * A Source System's relationships (spec story 60): Current (in effect, filterable by
 * kind), and the Suggested / Accepted / Rejected lists, most confident first, that let an
 * owner or editor decide, add and delete. Any member can read the lists; changing them
 * needs the enhancement right and an active Workspace.
 */
export function RelationshipReview({
  workspace,
  systemId,
  initialView = "current",
}: {
  workspace: Workspace;
  systemId: string;
  /** The list it opens on: Current, or Suggested when the visit is to review. */
  initialView?: "current" | "suggested";
}) {
  const [status, setStatus] = useState<View>(initialView);
  const [kind, setKind] = useState<KindFilter>("all");
  const byStatus = {
    suggested: useRelationshipsByStatus(workspace.id, systemId, "suggested"),
    accepted: useRelationshipsByStatus(workspace.id, systemId, "accepted"),
    rejected: useRelationshipsByStatus(workspace.id, systemId, "rejected"),
  };
  const schema = useSourceSchema(workspace.id, systemId, true);
  const tables = useMemo(
    () => new Map((schema.data?.tables ?? []).map((t) => [t.id, t])),
    [schema.data],
  );
  const current = useMemo(() => {
    if (!schema.data || !byStatus.accepted.data) return null;
    return {
      edges: relationshipEdges(schema.data.tables, byStatus.accepted.data),
      tables,
    };
  }, [schema.data, byStatus.accepted.data, tables]);
  const counts: Record<View, number | undefined> = {
    current: current?.edges.length,
    suggested: byStatus.suggested.data?.length,
    accepted: byStatus.accepted.data?.length,
    rejected: byStatus.rejected.data?.length,
  };
  const list = byStatus[status === "current" ? "accepted" : status];
  const decide = useDecideRelationship(workspace.id, systemId);
  const canDecide =
    workspace.status !== "archived" && allows(workspace, "source_schema.enhance");
  const members = useMembers(workspace.id);
  const names = new Map((members.data ?? []).map((m) => [m.user_id, m.display_name]));
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<DeleteTarget | null>(null);
  const [accepting, setAccepting] = useState<{
    id: string;
    from: Endpoint;
    to: Endpoint;
    cardinality: string;
  } | null>(null);

  const askDelete = (relationship: Relationship) => {
    const ends = relationshipEnds(relationship, tables);
    const by = relationship.decided_by && names.get(relationship.decided_by);
    setDeleting({
      id: relationship.id,
      manual: relationship.origin === "manual",
      ...ends,
      kind: relationship.origin === "manual" ? "Manually added" : "Inferred, accepted",
      confidence: relationship.confidence,
      decidedBy: by
        ? `${relationship.origin === "manual" ? "Added" : "Accepted"} by ${by}`
        : "",
    });
  };
  const askAccept = (relationship: Relationship) =>
    setAccepting({ id: relationship.id, ...relationshipEnds(relationship, tables) });

  const shownEdges = (current?.edges ?? []).filter((e) => kind === "all" || e.kind === kind);
  const kindCount = (k: KindFilter) =>
    (current?.edges ?? []).filter((e) => k === "all" || e.kind === k).length;

  return (
    <section aria-label="Relationships" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Tabs
          value={status}
          onValueChange={(value) => setStatus(value as View)}
        >
          <TabsList className="px-0">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.status} value={tab.status} className="text-sm">
                {tab.label}
                {counts[tab.status] !== undefined && (
                  <span className="ms-1 font-normal text-subtle-foreground tabular-nums">
                    {counts[tab.status]}
                  </span>
                )}
                {tab.status === "suggested" && (counts.suggested ?? 0) > 0 && (
                  <span aria-hidden className="ms-1 size-1.5 rounded-full bg-warning" />
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        {canDecide && status === "current" && current && (
          <Button
            type="button"
            variant="primary"
            className="ms-auto"
            onClick={() => setAdding(true)}
          >
            <Plus aria-hidden />
            Add relationship
          </Button>
        )}
      </div>
      {canDecide && current && (
        <AddRelationshipDialog
          open={adding}
          onOpenChange={setAdding}
          workspaceId={workspace.id}
          systemId={systemId}
          tables={schema.data?.tables ?? []}
          edges={current.edges}
        />
      )}
      <DeleteRelationshipDialog
        target={deleting}
        onClose={() => setDeleting(null)}
        workspaceId={workspace.id}
        systemId={systemId}
      />
      <AcceptRejectedDialog
        target={accepting}
        onClose={() => setAccepting(null)}
        workspaceId={workspace.id}
        systemId={systemId}
      />
      {status === "current" &&
        (schema.isError || byStatus.accepted.isError ? (
          <ErrorText>
            Could not load the relationships:{" "}
            {(schema.error ?? byStatus.accepted.error)?.message}
          </ErrorText>
        ) : current ? (
          <>
            <div role="group" aria-label="Filter by kind" className="flex flex-wrap gap-2">
              {KIND_FILTERS.map((f) => (
                <Button
                  key={f.kind}
                  type="button"
                  size="sm"
                  variant="secondary"
                  className={
                    kind === f.kind
                      ? "border-primary bg-primary-subtle text-foreground"
                      : undefined
                  }
                  aria-pressed={kind === f.kind}
                  onClick={() => setKind(f.kind)}
                >
                  {f.label}
                  <span className="tabular-nums">{kindCount(f.kind)}</span>
                </Button>
              ))}
            </div>
            <RelationshipsTable
              edges={shownEdges}
              tables={current.tables}
              empty="No relationships of this kind."
              onDelete={
                canDecide
                  ? (edge) => {
                      const relationship = byStatus.accepted.data?.find(
                        (r) => r.id === edge.relationshipId,
                      );
                      if (relationship) askDelete(relationship);
                    }
                  : undefined
              }
            />
          </>
        ) : (
          <Loading label="Loading relationships…" className="p-0" />
        ))}
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {status !== "current" && list.isPending && (
        <Loading label="Loading relationships…" className="p-0" />
      )}
      {status !== "current" && list.isError && (
        <ErrorText>
          Could not load the relationships: {list.error.message}
        </ErrorText>
      )}
      {status !== "current" && list.isSuccess && list.data.length === 0 && (
        <EmptyState>No {status} relationships.</EmptyState>
      )}
      {status !== "current" && list.isSuccess && list.data.length > 0 && (
        <TableCard>
          <Table aria-label={`${status} relationships`}>
            <TableHeader>
              <TableRow>
                <TableHead>Relationship</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>Evidence</TableHead>
                <TableHead>{status === "suggested" ? "Origin" : "Kind"}</TableHead>
                {status !== "suggested" && <TableHead>Decided</TableHead>}
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((relationship) => {
                const ends = relationshipEnds(relationship, tables);
                return (
                  <TableRow key={relationship.id}>
                    <TableCell className="whitespace-normal">
                      <RelationshipVisual
                        {...ends}
                        dashed
                        muted={relationship.status === "rejected"}
                      />
                    </TableCell>
                    <TableCell>
                      <Confidence ratio={relationship.confidence} />
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <Evidence relationship={relationship} />
                    </TableCell>
                    <TableCell>
                      <Badge variant={relationship.origin === "manual" ? "neutral" : "mapped"}>
                        {originLabel(relationship.origin)}
                      </Badge>
                    </TableCell>
                    {status !== "suggested" && (
                      <TableCell className="whitespace-normal">
                        <DecidedBy
                          verb={
                            relationship.status === "rejected"
                              ? "Rejected by"
                              : relationship.origin === "manual"
                                ? "Added by"
                                : "Accepted by"
                          }
                          relationship={relationship}
                          names={names}
                        />
                      </TableCell>
                    )}
                    <TableCell>
                      <Decision
                        relationship={relationship}
                        canDecide={canDecide}
                        decide={decide}
                        onDelete={canDecide ? askDelete : undefined}
                        onAcceptRejected={canDecide ? askAccept : undefined}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableCard>
      )}
    </section>
  );
}


/**
 * One table's suggested and accepted inferred relationships, from either end, on the
 * table page of the Source Schema browser; suggested ones can be decided in place, with
 * the same right as the review.
 */
export function TableRelationships({
  workspace,
  systemId,
  tableId,
}: {
  workspace: Workspace;
  systemId: string;
  tableId: string;
}) {
  const suggested = useRelationshipsByStatus(workspace.id, systemId, "suggested");
  const accepted = useRelationshipsByStatus(workspace.id, systemId, "accepted");
  const decide = useDecideRelationship(workspace.id, systemId);
  const canDecide =
    workspace.status !== "archived" && allows(workspace, "source_schema.enhance");
  const mine = [...(suggested.data ?? []), ...(accepted.data ?? [])].filter(
    (r) => r.from_column.table_id === tableId || r.to_column.table_id === tableId,
  );
  const failed = suggested.error ?? accepted.error;

  return (
    <>
      <h3 className={h5Classes}>
        Inferred relationships{" "}
        <span className="font-normal text-subtle-foreground tabular-nums">
          {mine.length}
        </span>
      </h3>
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {failed ? (
        <ErrorText>Could not load the relationships: {failed.message}</ErrorText>
      ) : suggested.isPending || accepted.isPending ? (
        <Loading label="Loading relationships…" className="p-0" />
      ) : mine.length === 0 ? (
        <EmptyState>No inferred relationships for this table.</EmptyState>
      ) : (
        <TableCard>
          <Table aria-label="Inferred relationships">
            <TableHeader>
              <TableRow>
                <TableHead>Relationship</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>
                  <span className="sr-only">Decision</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {mine.map((relationship) => (
                <TableRow key={relationship.id}>
                  <TableCell className="whitespace-normal">
                    <span className="inline-flex flex-wrap gap-1">
                      <ColumnRef column={relationship.from_column} />
                      <span aria-hidden className="text-subtle-foreground">
                        →
                      </span>
                      <ColumnRef column={relationship.to_column} />
                    </span>
                  </TableCell>
                  <TableCell>
                    <Confidence ratio={relationship.confidence} />
                  </TableCell>
                  <TableCell>
                    <Decision
                      relationship={relationship}
                      canDecide={canDecide}
                      decide={decide}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
    </>
  );
}
