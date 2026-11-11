"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Network } from "lucide-react";
import { useEffect, useState } from "react";

import { isActive, useJobs } from "../api/jobs";
import {
  relationshipsKey,
  useDecideRelationship,
  useRelationshipsByStatus,
  useStartInference,
  type Relationship,
  type RelationshipStatus,
} from "../api/relationships";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Loading } from "../shell/Loading";
import { EmptyState, ErrorText, h4Classes, monoClasses } from "./parts";

const TABS: { status: RelationshipStatus; label: string }[] = [
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
 * The ER diagram shows it in its header, as the page's primary action.
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
        variant="primary"
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

/**
 * A Source System's inferred relationships (spec story 60): the Suggested / Accepted /
 * Rejected lists, most confident first, let an owner or editor decide each one. Any member
 * can read the lists; deciding needs the enhancement right and an active Workspace.
 */
export function RelationshipReview({
  workspace,
  systemId,
}: {
  workspace: Workspace;
  systemId: string;
}) {
  const [status, setStatus] = useState<RelationshipStatus>("suggested");
  const list = useRelationshipsByStatus(workspace.id, systemId, status);
  const decide = useDecideRelationship(workspace.id, systemId);
  const canDecide =
    workspace.status !== "archived" && allows(workspace, "source_schema.enhance");

  return (
    <section aria-label="Relationships" className="flex flex-col gap-3">
      <h4 className={h4Classes}>Relationships</h4>
      <Tabs
        value={status}
        onValueChange={(value) => setStatus(value as RelationshipStatus)}
      >
        <TabsList className="px-0">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.status} value={tab.status}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {list.isPending && (
        <Loading label="Loading relationships…" className="p-0" />
      )}
      {list.isError && (
        <ErrorText>
          Could not load the relationships: {list.error.message}
        </ErrorText>
      )}
      {list.isSuccess && list.data.length === 0 && (
        <EmptyState>No {status} relationships.</EmptyState>
      )}
      {list.isSuccess && list.data.length > 0 && (
        <ul className="flex flex-col gap-3">
          {list.data.map((relationship) => (
            <li
              key={relationship.id}
              className="flex flex-col gap-2 rounded-md border border-border p-3"
            >
              <div className={`${monoClasses} flex flex-col break-all`}>
                <span>{columnName(relationship.from_column)}</span>
                <span aria-hidden="true">→</span>
                <span>{columnName(relationship.to_column)}</span>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="accent">
                  {percent(relationship.confidence)}
                </Badge>
                <Badge>{relationship.origin}</Badge>
                {signalChips(relationship).map((chip) => (
                  <Badge key={chip} variant="info">
                    {chip}
                  </Badge>
                ))}
              </div>
              {canDecide && relationship.status === "suggested" && (
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="primary"
                    disabled={decide.isPending}
                    onClick={() =>
                      decide.mutate({ id: relationship.id, decision: "accept" })
                    }
                  >
                    Accept
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={decide.isPending}
                    onClick={() =>
                      decide.mutate({ id: relationship.id, decision: "reject" })
                    }
                  >
                    Reject
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
