"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { isActive, useJobs } from "../api/jobs";
import {
  piiFindingsKey,
  useDecidePiiFinding,
  usePiiFindings,
  useStartPiiScan,
  type PiiStatus,
} from "../api/pii";
import { sourceSummaryKey } from "../api/sourceSummary";
import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
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
import {
  Actions,
  EmptyState,
  ErrorText,
  TableCard,
  h3Classes,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

const TABS: readonly { status: PiiStatus; label: string; empty: string }[] = [
  {
    status: "suggested",
    label: "To review",
    empty: "Nothing to review. Scan the values to look for more.",
  },
  { status: "confirmed", label: "Confirmed", empty: "No confirmed findings." },
  { status: "dismissed", label: "Dismissed", empty: "No dismissed findings." },
];

const CATEGORY_LABELS = {
  direct_identifier: "Direct identifier",
  quasi_identifier: "Quasi-identifier",
  sensitive: "Sensitive",
  financial: "Financial",
} as const;

/**
 * A Source System's PII folder: a value scan started as a background job, and the findings
 * in three tabs. Members with the review right confirm or dismiss; others read them. Both the
 * lists and the Source summary's count refresh after a decision and when a scan finishes.
 */
export function PiiPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canReview = allows(workspace, "pii.review");
  const [tab, setTab] = useState<PiiStatus>("suggested");
  const [scanOpen, setScanOpen] = useState(false);
  const findings = usePiiFindings(workspace.id, system.id, tab);
  const scan = useStartPiiScan(workspace.id, system.id);
  const decide = useDecidePiiFinding(workspace.id, system.id);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();

  const startedId = scan.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: piiFindingsKey(workspace.id, system.id),
      });
      void queryClient.invalidateQueries({
        queryKey: sourceSummaryKey(workspace.id, system.id),
      });
    }
  }, [finished, queryClient, workspace.id, system.id]);

  const current = TABS.find((t) => t.status === tab)!;

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="folder-title" className={h3Classes}>
          PII
        </h3>
        {canReview && (
          <Actions className="ml-auto">
            <Button
              type="button"
              variant="primary"
              disabled={scan.isPending}
              onClick={() => setScanOpen(true)}
            >
              Scan values
            </Button>
          </Actions>
        )}
      </div>
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
      <Tabs value={tab} onValueChange={(value) => setTab(value as PiiStatus)}>
        <TabsList className="px-0">
          {TABS.map((t) => (
            <TabsTrigger key={t.status} value={t.status}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {findings.isPending && <Loading className="p-0" />}
      {findings.isError && (
        <ErrorText>
          Could not load the findings: {findings.error.message}
        </ErrorText>
      )}
      {findings.isSuccess && findings.data.length === 0 && (
        <EmptyState>{current.empty}</EmptyState>
      )}
      {findings.isSuccess && findings.data.length > 0 && (
        <TableCard>
          <Table aria-label={`${current.label} findings`}>
            <TableHeader>
              <TableRow>
                <TableHead>Column</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Rule</TableHead>
                <TableHead className="text-right">Confidence</TableHead>
                <TableHead>Evidence</TableHead>
                {canReview && <TableHead />}
              </TableRow>
            </TableHeader>
            <TableBody>
              {findings.data.map((finding) => (
                <TableRow key={finding.id}>
                  <TableCell className={`${monoClasses} max-w-64 truncate`}>
                    {finding.db_schema}.{finding.table}.{finding.column}
                    {finding.is_protected && (
                      <>
                        {" "}
                        <Badge variant="pii" className="font-sans">
                          Protected
                        </Badge>
                      </>
                    )}
                  </TableCell>
                  <TableCell>{CATEGORY_LABELS[finding.category]}</TableCell>
                  <TableCell className={monoClasses}>{finding.rule}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {Math.round(finding.confidence * 100)}%
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {finding.evidence}
                  </TableCell>
                  {canReview && (
                    <TableCell>
                      <Actions>
                        {finding.status !== "confirmed" && (
                          <Button
                            type="button"
                            disabled={decide.isPending}
                            onClick={() =>
                              decide.mutate({
                                id: finding.id,
                                decision: "confirm",
                              })
                            }
                          >
                            Confirm
                          </Button>
                        )}
                        {finding.status !== "dismissed" && (
                          <Button
                            type="button"
                            disabled={decide.isPending}
                            onClick={() =>
                              decide.mutate({
                                id: finding.id,
                                decision: "dismiss",
                              })
                            }
                          >
                            Dismiss
                          </Button>
                        )}
                      </Actions>
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

/** Which tables to scan: every table (not views) of the latest Snapshot, all ticked at first. */
function ScanDialog({
  workspace,
  system,
  pending,
  error,
  onClose,
  onStart,
}: {
  workspace: Workspace;
  system: SourceSystem;
  pending: boolean;
  error: Error | null;
  onClose: () => void;
  onStart: (tableIds: string[]) => void;
}) {
  const schema = useSourceSchema(workspace.id, system.id, true);
  const tables = (schema.data?.tables ?? []).filter(
    (t) => t.kind === "table" && t.status !== "source_removed",
  );
  const [unticked, setUnticked] = useState<ReadonlySet<string>>(new Set());
  const chosen = tables.filter((t) => !unticked.has(t.id));

  function toggle(id: string, checked: boolean) {
    setUnticked((last) => {
      const next = new Set(last);
      if (checked) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Scan values</DialogTitle>
          <DialogDescription>
            Samples rows of the tables you choose and tests the values for
            national IDs, IBANs, mobile numbers, emails and dates of birth.
            Sampled values are never stored.
          </DialogDescription>
        </DialogHeader>
        {schema.isPending && <Loading className="p-0" />}
        {schema.isError && (
          <ErrorText>
            Could not load the tables: {schema.error.message}
          </ErrorText>
        )}
        {schema.isSuccess && tables.length === 0 && (
          <EmptyState>This system has no tables to scan yet.</EmptyState>
        )}
        {tables.length > 0 && (
          <>
            <Actions>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setUnticked(new Set())}
              >
                Select all
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setUnticked(new Set(tables.map((t) => t.id)))}
              >
                Select none
              </Button>
            </Actions>
            <ul
              aria-label="Tables to scan"
              className="flex max-h-72 flex-col gap-2 overflow-y-auto"
            >
              {tables.map((t) => {
                const name = `${t.db_schema}.${t.name}`;
                return (
                  <li key={t.id} className="flex items-center gap-2">
                    <Checkbox
                      id={`scan-${t.id}`}
                      aria-label={name}
                      checked={!unticked.has(t.id)}
                      onCheckedChange={(checked) =>
                        toggle(t.id, checked === true)
                      }
                    />
                    <label htmlFor={`scan-${t.id}`} className={monoClasses}>
                      {name}
                    </label>
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {error && <ErrorText>{error.message}</ErrorText>}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="ghost">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="primary"
            disabled={pending || chosen.length === 0}
            onClick={() => onStart(chosen.map((t) => t.id))}
          >
            Start scan
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
