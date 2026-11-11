"use client";

import { Check, Trash2, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";

import {
  useAddRelationship,
  useDecideRelationship,
  useDeleteRelationship,
} from "../api/relationships";
import type { SourceSchema } from "../api/snapshots";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
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
import { isUnique, presentColumns, qualified, type ErEdge } from "./erGraph";
import { ErrorText, NativeSelect, labelClasses, monoClasses } from "./parts";
import { RelationshipVisual, targetTag, type Endpoint } from "./RelationshipVisual";

type SchemaTable = SourceSchema["tables"][number];

/** One end of the relationship: a table id and one of its column names. */
interface Side {
  tableId: string;
  column: string;
}

const NONE: Side = { tableId: "", column: "" };

function ColumnPicker({
  legend,
  tables,
  value,
  onChange,
}: {
  legend: string;
  tables: readonly SchemaTable[];
  value: Side;
  onChange: (side: Side) => void;
}) {
  const table = tables.find((t) => t.id === value.tableId);
  return (
    <fieldset className="m-0 grid min-w-0 grid-cols-2 gap-3 rounded-lg border border-border bg-surface p-3">
      <legend className="sr-only">{legend}</legend>
      <label className={labelClasses}>
        {legend} table
        <NativeSelect
          className={monoClasses}
          value={value.tableId}
          onChange={(event) => onChange({ tableId: event.target.value, column: "" })}
        >
          <option value="">Choose a table</option>
          {tables.map((t) => (
            <option key={t.id} value={t.id}>
              {qualified(t)}
            </option>
          ))}
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        {legend} column
        <NativeSelect
          className={monoClasses}
          value={value.column}
          disabled={!table}
          onChange={(event) => onChange({ ...value, column: event.target.value })}
        >
          <option value="">Choose a column</option>
          {table &&
            presentColumns(table).map((c) => (
              <option key={c.id} value={c.name}>
                {c.name} · {c.data_type}
                {c.is_pk ? " · PK" : isUnique(table, [c.name]) ? " · unique" : ""}
              </option>
            ))}
        </NativeSelect>
      </label>
    </fieldset>
  );
}

/**
 * Add a relationship by hand (spec §6.6): from table and column to table and column, one
 * column to one column. A live preview draws the relationship and a checklist says whether
 * the pair is new, the types match and the target is unique. Saving needs no confirmation:
 * the relationship is accepted at once. A self reference, a declared foreign key and an
 * already accepted pair are blocked; differing types or a target that is not unique only
 * warn.
 */
export function AddRelationshipDialog({
  open,
  onOpenChange,
  workspaceId,
  systemId,
  tables,
  edges,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  systemId: string;
  tables: readonly SchemaTable[];
  /** The relationships in effect, to block a pair that is already one. */
  edges: readonly ErEdge[];
}) {
  const add = useAddRelationship(workspaceId, systemId);
  const [from, setFrom] = useState<Side>(NONE);
  const [to, setTo] = useState<Side>(NONE);
  const present = useMemo(
    () => tables.filter((t) => !t.status || t.status === "present"),
    [tables],
  );
  const fromTable = present.find((t) => t.id === from.tableId);
  const toTable = present.find((t) => t.id === to.tableId);
  const fromColumn = fromTable?.columns.find((c) => c.name === from.column);
  const toColumn = toTable?.columns.find((c) => c.name === to.column);

  let blocked: string | null = null;
  let preview: { from: Endpoint; to: Endpoint; cardinality: string } | null = null;
  const checks: { state: "ok" | "warn" | "blocked"; text: string }[] = [];
  if (fromTable && toTable && fromColumn && toColumn) {
    const samePair = (e: ErEdge) =>
      e.fromCols.length === 1 &&
      ((e.from === fromTable.id &&
        e.to === toTable.id &&
        e.fromCols[0] === fromColumn.name &&
        e.toCols[0] === toColumn.name) ||
        (e.from === toTable.id &&
          e.to === fromTable.id &&
          e.fromCols[0] === toColumn.name &&
          e.toCols[0] === fromColumn.name));
    if (fromColumn.id === toColumn.id) {
      blocked = "A column can't reference itself.";
    } else if (edges.some((e) => e.kind === "declared" && samePair(e))) {
      blocked = "The source database already declares this foreign key.";
    } else if (edges.some((e) => e.kind !== "declared" && samePair(e))) {
      blocked = "This pair is already an accepted relationship.";
    }
    const typesMatch = fromColumn.data_type === toColumn.data_type;
    const targetUnique = isUnique(toTable, [toColumn.name]);
    checks.push(
      blocked
        ? { state: "blocked", text: blocked }
        : { state: "ok", text: "This pair is not a relationship yet." },
      typesMatch
        ? { state: "ok", text: "The column types match." }
        : { state: "warn", text: "The two columns have different types." },
      targetUnique
        ? { state: "ok", text: "The target column is a primary key or unique." }
        : { state: "warn", text: "The target column is not a primary key or unique." },
    );
    preview = {
      from: {
        tableId: fromTable.id,
        schema: fromTable.db_schema,
        table: fromTable.name,
        columns: fromColumn.name,
        tag: "FK",
      },
      to: {
        tableId: toTable.id,
        schema: toTable.db_schema,
        table: toTable.name,
        columns: toColumn.name,
        tag: targetTag(toTable, [toColumn.name]),
      },
      cardinality: isUnique(fromTable, [fromColumn.name]) ? "1:1" : "N:1",
    };
  }

  function onChange(next: boolean) {
    if (!next) {
      setFrom(NONE);
      setTo(NONE);
      add.reset();
    }
    onOpenChange(next);
  }

  return (
    <Dialog open={open} onOpenChange={onChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add relationship</DialogTitle>
          <DialogDescription>
            Single column to single column only; composite joins are not supported.
          </DialogDescription>
        </DialogHeader>
        <ColumnPicker legend="From" tables={present} value={from} onChange={setFrom} />
        <ColumnPicker legend="To" tables={present} value={to} onChange={setTo} />
        {preview && (
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
            <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              Preview <Badge>Manually added</Badge>
            </span>
            <RelationshipVisual
              from={preview.from}
              to={preview.to}
              cardinality={preview.cardinality}
              dashed
            />
            <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
              {checks.map((c) => (
                <li key={c.text} className="flex items-center gap-2">
                  {c.state === "blocked" ? (
                    <X aria-hidden className="size-4 text-danger-strong" />
                  ) : c.state === "ok" ? (
                    <Check aria-hidden className="size-4 text-success-strong" />
                  ) : (
                    <TriangleAlert aria-hidden className="size-4 text-warning-strong" />
                  )}
                  <span>{c.text}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {add.isError && <ErrorText>{add.error.message}</ErrorText>}
        <DialogFooter className="items-center">
          <span className="me-auto text-xs text-subtle-foreground">
            Saved as accepted. The Data Warehouse changes only when you regenerate it.
          </span>
          <DialogClose asChild>
            <Button type="button" variant="ghost">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="primary"
            disabled={!fromColumn || !toColumn || blocked !== null || add.isPending}
            onClick={() =>
              fromColumn &&
              toColumn &&
              add.mutate(
                { from_column_id: fromColumn.id, to_column_id: toColumn.id },
                {
                  onSuccess: () => {
                    toast.success("Relationship added.");
                    onChange(false);
                  },
                },
              )
            }
          >
            Add relationship
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** What the delete dialog shows of the relationship it is about. */
export interface DeleteTarget {
  id: string;
  manual: boolean;
  from: Endpoint;
  to: Endpoint;
  cardinality: string;
  /** "Declared", "Inferred, accepted" or "Manually added". */
  kind: string;
  confidence: number | null;
  /** "Added by Ada, 1/6/2026" or similar; empty when unknown. */
  decidedBy: string;
}

/** Confirm deleting one relationship; a manual one is removed, an inferred one rejected. */
export function DeleteRelationshipDialog({
  target,
  onClose,
  workspaceId,
  systemId,
}: {
  target: DeleteTarget | null;
  onClose: () => void;
  workspaceId: string;
  systemId: string;
}) {
  const remove = useDeleteRelationship(workspaceId, systemId);
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset();
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <span className="grid size-9 place-items-center rounded-full bg-danger-subtle text-danger-strong">
            <Trash2 aria-hidden className="size-4" />
          </span>
          <DialogTitle>Delete relationship</DialogTitle>
          <DialogDescription>
            {target?.manual
              ? "This relationship was added by hand."
              : "This relationship was inferred."}
          </DialogDescription>
        </DialogHeader>
        {target && (
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
            <RelationshipVisual
              from={target.from}
              to={target.to}
              cardinality={target.cardinality}
              dashed
            />
            <p className="m-0 text-xs text-muted-foreground">
              {target.kind}
              {" · "}
              {target.confidence == null
                ? "no confidence"
                : `${Math.round(target.confidence * 100)}% confidence`}
              {target.decidedBy && ` · ${target.decidedBy}`}
            </p>
          </div>
        )}
        <ul className="m-0 flex list-disc flex-col gap-1 ps-5 text-sm text-muted-foreground">
          <li>
            {target?.manual
              ? "Removed: it is gone from the Source schema and the ER diagram."
              : "Moves to Rejected: it is not suggested again."}
          </li>
          <li>The Data Warehouse does not change until you regenerate it.</li>
        </ul>
        {remove.isError && <ErrorText>{remove.error.message}</ErrorText>}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="ghost">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() =>
              target &&
              remove.mutate(target.id, {
                onSuccess: () => {
                  toast.success(target.manual ? "Relationship deleted." : "Moved to Rejected.");
                  onClose();
                },
              })
            }
          >
            {target?.manual ? "Delete" : "Move to Rejected"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Confirm accepting a rejected relationship (it keeps its origin). */
export function AcceptRejectedDialog({
  target,
  onClose,
  workspaceId,
  systemId,
}: {
  target: { id: string; from: Endpoint; to: Endpoint; cardinality: string } | null;
  onClose: () => void;
  workspaceId: string;
  systemId: string;
}) {
  const decide = useDecideRelationship(workspaceId, systemId);
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) {
          decide.reset();
          onClose();
        }
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Accept relationship</DialogTitle>
          <DialogDescription>
            It moves from Rejected to Accepted and counts in the Source schema again. The Data
            Warehouse does not change until you regenerate it.
          </DialogDescription>
        </DialogHeader>
        {target && (
          <div className="rounded-lg border border-border bg-surface p-3">
            <RelationshipVisual
              from={target.from}
              to={target.to}
              cardinality={target.cardinality}
              dashed
            />
          </div>
        )}
        {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="ghost">
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="primary"
            disabled={decide.isPending}
            onClick={() =>
              target &&
              decide.mutate(
                { id: target.id, decision: "accept" },
                {
                  onSuccess: () => {
                    toast.success("Relationship accepted.");
                    onClose();
                  },
                },
              )
            }
          >
            Accept relationship
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
