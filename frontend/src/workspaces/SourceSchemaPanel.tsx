"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ChevronDown,
  Database,
  Download,
  FolderInput,
  History,
  RefreshCw,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { dataDictionaryUrl, useSaveDataDictionary } from "../api/files";
import { isActive, useJobs } from "../api/jobs";
import { useRelationshipsByStatus } from "../api/relationships";
import {
  tableProfilesKey,
  useImportStatus,
  useProfilingRuns,
  useStartProfiling,
} from "../api/profiling";
import {
  snapshotsKey,
  sourceSchemaKey,
  useSnapshots,
  useSourceSchema,
  useStartExtraction,
  type SnapshotSummary,
} from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { toast } from "../components/ui/sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { cn } from "../lib/utils";
import { FindRelationships, RelationshipReview } from "./RelationshipReview";
import { SchemaBrowser, Sep } from "./SchemaBrowser";
import { SnapshotDiffView } from "./SnapshotDiffView";
import { EmptyState, ErrorText, listCardClasses } from "./parts";
import { Loading } from "../shell/Loading";

/** A count and its noun, singular for one. */
function Counted({
  count,
  one,
  many,
}: {
  count: number;
  one: string;
  many: string;
}) {
  return (
    <span>
      <span className="font-medium text-foreground">{count}</span>{" "}
      {count === 1 ? one : many}
    </span>
  );
}

/** A Snapshot's counts, as the latest-Snapshot line and the Snapshot list show them. */
function SnapshotCounts({ snapshot }: { snapshot: SnapshotSummary }) {
  return (
    <>
      <Counted
        count={snapshot.schema_count}
        one="Database Schema"
        many="Database Schemas"
      />
      <Sep />
      <Counted
        count={snapshot.table_count}
        one="table and view"
        many="tables and views"
      />
      <Sep />
      <Counted count={snapshot.column_count} one="column" many="columns" />
      <Sep />
      <Counted count={snapshot.routine_count} one="routine" many="routines" />
    </>
  );
}

/**
 * A Source System's Source Schema (spec stories 45, 46, 54-57): the latest Snapshot's
 * counts under the title with every Snapshot behind a toggle, the browser over the latest
 * Snapshot, and the diff between Snapshots. Owners and editors extract metadata from the
 * Connection, profile every table and view and find relationships, each as a background
 * job; the page refreshes once a job finishes. The Relationships tab reviews the inferred
 * relationships (`?tab=relationships` opens it, as the ER diagram's link does).
 */
export function SourceSchemaPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const canExtract = allows(workspace, "source_system.extract");
  const snapshots = useSnapshots(workspace.id, system.id);
  const hasSnapshot = snapshots.isSuccess && snapshots.data.length > 0;
  const schema = useSourceSchema(workspace.id, system.id, hasSnapshot);
  const extract = useStartExtraction(workspace.id, system.id);
  const canSave = allows(workspace, "file.upload");
  const save = useSaveDataDictionary(workspace.id, system.id);
  const importStatus = useImportStatus(workspace.id, system.id);
  const profileAll = useStartProfiling(workspace.id, system.id);
  const runs = useProfilingRuns(workspace.id, system.id);
  const jobs = useJobs(workspace.id);
  const queryClient = useQueryClient();
  const [showSnapshots, setShowSnapshots] = useState(false);
  const searchParams = useSearchParams();
  const [tab, setTab] = useState(
    searchParams.get("tab") === "relationships" ? "relationships" : "browse",
  );
  const suggested = useRelationshipsByStatus(workspace.id, system.id, "suggested");

  const startedId = extract.data?.job_id;
  const started = jobs.data?.items.find((job) => job.id === startedId);
  const finished = started != null && !isActive(started);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: snapshotsKey(workspace.id, system.id),
      });
      void queryClient.invalidateQueries({
        queryKey: sourceSchemaKey(workspace.id, system.id),
      });
    }
  }, [finished, queryClient, workspace.id, system.id]);

  const unavailable =
    importStatus.data?.imported === true && !importStatus.data.has_connection;
  const canProfile =
    allows(workspace, "source_system.profile") &&
    importStatus.isSuccess &&
    !unavailable;
  const present = (schema.data?.tables ?? []).filter(
    (t) => t.status == null || t.status === "present",
  );
  const latest = snapshots.data?.find((s) => s.is_latest);
  const refreshing = snapshots.isFetching || schema.isFetching;

  /** Reload what the page already shows; no query is added. */
  function refresh() {
    for (const queryKey of [
      snapshotsKey(workspace.id, system.id),
      sourceSchemaKey(workspace.id, system.id),
      tableProfilesKey(workspace.id, system.id),
    ]) {
      void queryClient.invalidateQueries({ queryKey });
    }
  }

  function startProfileAll() {
    const tableIds = present.map((t) => t.id);
    profileAll.mutate(tableIds, {
      onSuccess: (job) => {
        runs.follow({ jobId: job.job_id, tableIds });
        toast.success(
          `Profiling started for ${tableIds.length} tables and views. Follow it under Background jobs.`,
        );
      },
    });
  }

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-5">
      <header className="flex flex-wrap items-start gap-4 border-b border-border pb-5">
        <div className="flex min-w-0 flex-1 basis-80 flex-col gap-2">
          <h1
            id="folder-title"
            className="text-2xl font-semibold tracking-tight text-balance text-foreground"
          >
            Source schema
          </h1>
          {hasSnapshot && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground tabular-nums">
              {latest && (
                <>
                  <Badge variant="accent">Latest</Badge>
                  <span>{new Date(latest.taken_at).toLocaleString()}</span>
                  <Sep />
                  <SnapshotCounts snapshot={latest} />
                  <Sep />
                </>
              )}
              <button
                type="button"
                aria-expanded={showSnapshots}
                aria-controls="snapshot-list"
                onClick={() => setShowSnapshots((shown) => !shown)}
                className="inline-flex cursor-pointer items-center gap-1 rounded-sm text-muted-foreground transition-colors hover:text-foreground [&_svg]:size-3.5"
              >
                <History aria-hidden />
                {snapshots.data.length === 1
                  ? "1 snapshot"
                  : `${snapshots.data.length} snapshots`}
                <ChevronDown
                  aria-hidden
                  className={cn(
                    "transition-transform",
                    showSnapshots && "rotate-180",
                  )}
                />
              </button>
            </div>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh"
            title="Refresh the Snapshots, the Source Schema and the profiles"
            onClick={refresh}
          >
            <RefreshCw
              aria-hidden
              className={cn(refreshing && "motion-safe:animate-spin")}
            />
          </Button>
          {hasSnapshot && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button">
                  <Download aria-hidden />
                  Data dictionary
                  <ChevronDown aria-hidden className="text-subtle-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-48">
                <DropdownMenuItem asChild>
                  <a href={dataDictionaryUrl(workspace.id, system.id)} download>
                    <Download aria-hidden />
                    Download (XLSX)
                  </a>
                </DropdownMenuItem>
                {canSave && (
                  <DropdownMenuItem
                    disabled={save.isPending}
                    onSelect={() =>
                      save.mutate(undefined, {
                        onSuccess: (file) =>
                          toast.success(`Saved ${file.name} to the file area.`),
                      })
                    }
                  >
                    <FolderInput aria-hidden />
                    Save to files
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {canProfile && present.length > 0 && (
            <Button
              type="button"
              disabled={profileAll.isPending}
              onClick={startProfileAll}
            >
              <Activity aria-hidden />
              Profile all tables
            </Button>
          )}
          {hasSnapshot && (
            <FindRelationships
              workspace={workspace}
              systemId={system.id}
              onStarted={() => setTab("relationships")}
            />
          )}
          {canExtract && (
            <Button
              type="button"
              variant="primary"
              disabled={extract.isPending}
              onClick={() =>
                extract.mutate(undefined, {
                  onSuccess: () =>
                    toast.success(
                      "Extraction started. Follow it under Background jobs; a new Snapshot appears here only if something changed.",
                    ),
                })
              }
            >
              <Database aria-hidden />
              Extract metadata
            </Button>
          )}
        </div>
      </header>
      {extract.isError && <ErrorText>{extract.error.message}</ErrorText>}
      {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      {profileAll.isError && <ErrorText>{profileAll.error.message}</ErrorText>}
      {hasSnapshot && showSnapshots && (
        <ul
          id="snapshot-list"
          aria-label="Snapshots"
          className={`${listCardClasses} [&_li]:flex [&_li]:flex-wrap [&_li]:items-center [&_li]:gap-2 [&_li]:px-3 [&_li]:py-2 [&_li]:text-sm [&_li]:tabular-nums`}
        >
          {snapshots.data.map((snapshot) => (
            <li key={snapshot.id}>
              <span className="text-foreground">
                {new Date(snapshot.taken_at).toLocaleString()}
              </span>
              {snapshot.is_latest && <Badge variant="accent">Latest</Badge>}
              <span className="flex flex-wrap items-center gap-2 text-muted-foreground">
                <SnapshotCounts snapshot={snapshot} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {snapshots.isPending && <Loading className="p-0" />}
      {snapshots.isError && (
        <ErrorText>
          Could not load the Snapshots: {snapshots.error.message}
        </ErrorText>
      )}
      {snapshots.isSuccess && snapshots.data.length === 0 && (
        <EmptyState>
          No Snapshot yet. Extract metadata from the Connection.
        </EmptyState>
      )}
      {hasSnapshot ? (
        <Tabs value={tab} onValueChange={setTab} className="gap-5">
          <TabsList className="px-0" aria-label="Source schema">
            <TabsTrigger value="browse">Tables and routines</TabsTrigger>
            <TabsTrigger value="relationships">
              Relationships
              {suggested.data && (
                <Badge
                  variant={suggested.data.length > 0 ? "accent" : "neutral"}
                  className="ms-1.5 tabular-nums"
                  title={`${suggested.data.length} suggested relationships to review`}
                >
                  {suggested.data.length}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>
          {/* Kept mounted, so the browser keeps its place while the review is open. */}
          <TabsContent
            value="browse"
            forceMount
            className="flex flex-col gap-5 data-[state=inactive]:hidden"
          >
            <SchemaBrowser
              workspace={workspace}
              systemId={system.id}
              profiling={runs.profiling}
              onProfilingStarted={runs.follow}
            />
            <SnapshotDiffView
              key={snapshots.data.map((s) => s.id).join()}
              workspaceId={workspace.id}
              systemId={system.id}
              snapshots={snapshots.data}
            />
          </TabsContent>
          <TabsContent value="relationships">
            <RelationshipReview workspace={workspace} systemId={system.id} />
          </TabsContent>
        </Tabs>
      ) : (
        snapshots.isSuccess && (
          <SnapshotDiffView
            key={snapshots.data.map((s) => s.id).join()}
            workspaceId={workspace.id}
            systemId={system.id}
            snapshots={snapshots.data}
          />
        )
      )}
    </section>
  );
}
