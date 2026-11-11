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
import { Loading } from "../shell/Loading";
import { AddRelationshipDialog, DeleteRelationshipDialog } from "./RelationshipDialogs";
import { RelationshipsTable } from "./RelationshipsTable";
import { relationshipEdges } from "./erGraph";
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
function Confidence({ ratio }: { ratio: number | null }) {
  if (ratio == null) {
    return <span className="text-subtle-foreground">—</span>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums">
      <span aria-hidden className="h-1 w-16 overflow-hidden rounded-full bg-muted">
        <span className="block h-full bg-primary" style={{ width: percent(ratio) }} />
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
}: {
  relationship: Relationship;
  canDecide: boolean;
  decide: ReturnType<typeof useDecideRelationship>;
  onDelete?: (relationship: Relationship) => void;
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
      <Badge>Rejected</Badge>
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
        variant="primary"
        disabled={decide.isPending}
        onClick={() => decide.mutate({ id: relationship.id, decision: "accept" })}
      >
        Accept
      </Button>
      <Button
        type="button"
        size="sm"
        disabled={decide.isPending}
        onClick={() => decide.mutate({ id: relationship.id, decision: "reject" })}
      >
        Reject
      </Button>
    </div>
  );
}

/**
 * A Source System's inferred relationships (spec story 60): the Suggested / Accepted /
 * Rejected lists, most confident first, as one table that lets an owner or editor decide
 * each one. Any member can read the lists; deciding needs the enhancement right and an
 * active Workspace.
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
  const byStatus = {
    suggested: useRelationshipsByStatus(workspace.id, systemId, "suggested"),
    accepted: useRelationshipsByStatus(workspace.id, systemId, "accepted"),
    rejected: useRelationshipsByStatus(workspace.id, systemId, "rejected"),
  };
  const schema = useSourceSchema(workspace.id, systemId, true);
  const current = useMemo(() => {
    if (!schema.data || !byStatus.accepted.data) return null;
    return {
      edges: relationshipEdges(schema.data.tables, byStatus.accepted.data),
      tables: new Map(schema.data.tables.map((t) => [t.id, t])),
    };
  }, [schema.data, byStatus.accepted.data]);
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
  const [deleting, setDeleting] = useState<{
    id: string;
    label: string;
    manual: boolean;
  } | null>(null);
  const askDelete = (relationship: Relationship) =>
    setDeleting({
      id: relationship.id,
      label: `${columnName(relationship.from_column)} → ${columnName(relationship.to_column)}`,
      manual: relationship.origin === "manual",
    });

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
      {status === "current" &&
        (schema.isError || byStatus.accepted.isError ? (
          <ErrorText>
            Could not load the relationships:{" "}
            {(schema.error ?? byStatus.accepted.error)?.message}
          </ErrorText>
        ) : current ? (
          <RelationshipsTable
            edges={current.edges}
            tables={current.tables}
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
                <TableHead>Origin</TableHead>
                <TableHead>
                  <span className="sr-only">Decision</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.data.map((relationship) => (
                <TableRow key={relationship.id}>
                  <TableCell className="whitespace-normal">
                    <div className="flex min-w-56 flex-col py-1">
                      <ColumnRef column={relationship.from_column} />
                      <span className="inline-flex gap-1">
                        <span aria-hidden className="text-subtle-foreground">
                          →
                        </span>
                        <ColumnRef column={relationship.to_column} />
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Confidence ratio={relationship.confidence} />
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {relationship.origin === "manual" ? (
                      <span className="text-muted-foreground">
                        Added by{" "}
                        {(relationship.decided_by &&
                          names.get(relationship.decided_by)) ||
                          "a member"}
                        {relationship.decided_at &&
                          ` · ${new Date(relationship.decided_at).toLocaleDateString()}`}
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {signalChips(relationship).map((chip) => (
                          <Badge key={chip} variant="info">
                            {chip}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge>
                      {relationship.origin === "manual"
                        ? "Manually added"
                        : relationship.origin}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Decision
                      relationship={relationship}
                      canDecide={canDecide}
                      decide={decide}
                      onDelete={canDecide ? askDelete : undefined}
                    />
                  </TableCell>
                </TableRow>
              ))}
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
