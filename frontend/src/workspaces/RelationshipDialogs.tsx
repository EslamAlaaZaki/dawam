"use client";

import { TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import type { SourceSchema } from "../api/snapshots";
import { useAddRelationship, useDeleteRelationship } from "../api/relationships";
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
import { Callout, ErrorText, NativeSelect, labelClasses, monoClasses } from "./parts";

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
    <fieldset className="m-0 grid min-w-0 grid-cols-2 gap-3 border-0 p-0">
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
                {c.name}
              </option>
            ))}
        </NativeSelect>
      </label>
    </fieldset>
  );
}

/**
 * Add a relationship by hand (spec §6.6): from table and column to table and column, one
 * column to one column. Both column types and the cardinality show once both are chosen.
 * Saving needs no confirmation: the relationship is accepted at once. A self reference, a
 * declared foreign key and an already accepted pair are blocked; differing types or a
 * target that is not unique only warn.
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
  const warnings: string[] = [];
  let cardinality = "";
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
    } else {
      if (fromColumn.data_type !== toColumn.data_type) {
        warnings.push("The two columns have different types.");
      }
      if (!isUnique(toTable, [toColumn.name])) {
        warnings.push("The target column is not a primary key or unique.");
      }
    }
    cardinality = isUnique(fromTable, [fromColumn.name]) ? "1:1" : "N:1";
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
            One column to one column; composite joins are not supported. It is accepted as
            soon as you save.
          </DialogDescription>
        </DialogHeader>
        <ColumnPicker legend="From" tables={present} value={from} onChange={setFrom} />
        <ColumnPicker legend="To" tables={present} value={to} onChange={setTo} />
        {fromColumn && toColumn && (
          <p className="m-0 text-sm text-muted-foreground">
            <span className={monoClasses}>{fromColumn.data_type}</span> →{" "}
            <span className={monoClasses}>{toColumn.data_type}</span>
            {" · "}
            {cardinality}
          </p>
        )}
        {blocked && <ErrorText>{blocked}</ErrorText>}
        {warnings.map((w) => (
          <Callout key={w} tone="warning" icon={<TriangleAlert aria-hidden />}>
            {w} You can still save it.
          </Callout>
        ))}
        {add.isError && <ErrorText>{add.error.message}</ErrorText>}
        <DialogFooter>
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

/** Confirm deleting one relationship; a manual one is removed, an inferred one rejected. */
export function DeleteRelationshipDialog({
  target,
  onClose,
  workspaceId,
  systemId,
}: {
  target: { id: string; label: string; manual: boolean } | null;
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
          <DialogTitle>Delete relationship</DialogTitle>
          <DialogDescription>
            <span className={monoClasses}>{target?.label}</span>
            <br />
            {target?.manual
              ? "This removes the relationship."
              : "This moves it to Rejected, and it is not suggested again."}{" "}
            Nothing in the Data Warehouse changes until you regenerate it.
          </DialogDescription>
        </DialogHeader>
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
                  toast.success("Relationship deleted.");
                  onClose();
                },
              })
            }
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
