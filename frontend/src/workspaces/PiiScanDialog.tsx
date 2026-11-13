"use client";

import { useState } from "react";

import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
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
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { Actions, EmptyState, ErrorText, monoClasses } from "./parts";

/**
 * Detect PII: which tables the value scan and the AI review, every table (not views) of the
 * latest Snapshot, grouped by schema, all ticked at first. Without a Connection only the AI
 * reviews them.
 */
export function ScanDialog({
  workspace,
  system,
  hasConnection,
  pending,
  error,
  onClose,
  onStart,
}: {
  workspace: Workspace;
  system: SourceSystem;
  hasConnection: boolean;
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
  const groups = [...new Set(tables.map((t) => t.db_schema))].map(
    (dbSchema) => [dbSchema, tables.filter((t) => t.db_schema === dbSchema)] as const,
  );

  function setTicked(ids: string[], checked: boolean) {
    setUnticked((last) => {
      const next = new Set(last);
      for (const id of ids) {
        if (checked) {
          next.delete(id);
        } else {
          next.add(id);
        }
      }
      return next;
    });
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Detect PII</DialogTitle>
          <DialogDescription>
            Find PII in the tables you choose. These jobs start together:
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col divide-y divide-border rounded-md border border-border">
          <JobRow
            name="Value scan"
            runs={hasConnection}
            text={
              hasConnection
                ? "Samples up to 1,000 rows per table and checks the values for national IDs, IBANs, mobile numbers, emails and dates of birth. Values are never stored."
                : "This system has no Connection, so no rows can be sampled. An owner can add one under Connection."
            }
          />
          <JobRow
            name="AI review"
            runs
            text="Reads the tables' names, types, comments and Documents, never their values, and suggests PII columns with a confidence. It may find none."
          />
        </div>
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
            <div className="flex items-center justify-between gap-2">
              <span
                aria-live="polite"
                className="text-sm text-muted-foreground tabular-nums"
              >
                <strong className="font-semibold text-foreground">
                  {chosen.length}
                </strong>{" "}
                of {tables.length} tables selected
              </span>
              <Actions>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setUnticked(new Set())}
                >
                  Select all
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setUnticked(new Set(tables.map((t) => t.id)))}
                >
                  Select none
                </Button>
              </Actions>
            </div>
            <div
              role="group"
              aria-label="Tables to scan"
              className="max-h-72 overflow-y-auto rounded-md border border-border"
            >
              {groups.map(([dbSchema, group]) => {
                const ticked = group.filter((t) => !unticked.has(t.id)).length;
                return (
                  <section key={dbSchema}>
                    <div className="sticky top-0 flex items-center gap-2 border-b border-border bg-muted px-3 py-1.5">
                      <Checkbox
                        id={`scan-schema-${dbSchema}`}
                        aria-label={`Schema ${dbSchema}`}
                        checked={
                          ticked === group.length
                            ? true
                            : ticked === 0
                              ? false
                              : "indeterminate"
                        }
                        onCheckedChange={(checked) =>
                          setTicked(
                            group.map((t) => t.id),
                            checked === true,
                          )
                        }
                      />
                      <label
                        htmlFor={`scan-schema-${dbSchema}`}
                        className={cn(monoClasses, "font-medium")}
                      >
                        {dbSchema}
                      </label>
                      <span className="ms-auto text-xs text-subtle-foreground tabular-nums">
                        {ticked} of {group.length}
                      </span>
                    </div>
                    <ul className="m-0 flex list-none flex-col p-0">
                      {group.map((t) => {
                        const columns = t.columns.filter(
                          (c) => c.status === "present",
                        ).length;
                        return (
                          <li
                            key={t.id}
                            className="flex items-center gap-2 px-3 py-1.5"
                          >
                            <Checkbox
                              id={`scan-${t.id}`}
                              aria-label={`${t.db_schema}.${t.name}`}
                              checked={!unticked.has(t.id)}
                              onCheckedChange={(checked) =>
                                setTicked([t.id], checked === true)
                              }
                            />
                            <label
                              htmlFor={`scan-${t.id}`}
                              className={monoClasses}
                            >
                              {t.name}
                            </label>
                            <span className="ms-auto text-xs text-subtle-foreground tabular-nums">
                              {columns} {columns === 1 ? "column" : "columns"}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                );
              })}
            </div>
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
            {chosen.length === 0
              ? "Start detection"
              : `Start detection · ${chosen.length} ${chosen.length === 1 ? "table" : "tables"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** One of the two jobs the dialog starts: its name, whether it runs and what it does. */
function JobRow({
  name,
  runs,
  text,
}: {
  name: string;
  runs: boolean;
  text: string;
}) {
  return (
    <div className="flex flex-col gap-1 px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span
          className={cn("text-sm font-medium", !runs && "text-muted-foreground")}
        >
          {name}
        </span>
        <Badge variant={runs ? "success" : "neutral"}>
          {runs ? "Runs" : "Skipped"}
        </Badge>
      </div>
      <p className="m-0 text-sm text-muted-foreground">{text}</p>
    </div>
  );
}
