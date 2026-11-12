"use client";

import { useState } from "react";

import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
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
import { Loading } from "../shell/Loading";
import { Actions, EmptyState, ErrorText, monoClasses } from "./parts";

/** Which tables to scan: every table (not views) of the latest Snapshot, all ticked at first. */
export function ScanDialog({
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
