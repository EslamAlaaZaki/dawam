"use client";

import { Lock, ShieldOff, Table2 } from "lucide-react";
import { useState } from "react";

import {
  CATEGORY_LABELS,
  PII_CATEGORIES,
  useAddPiiColumn,
  useDecidePiiColumn,
  useRemovePiiColumn,
  usePiiColumns,
  type PiiCategory,
  type PiiColumn,
} from "../api/pii";
import type { SourceSchema } from "../api/snapshots";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { toast } from "../components/ui/sonner";
import { cn } from "../lib/utils";
import { presentColumns } from "./erGraph";
import { ErrorText, labelClasses, monoClasses } from "./parts";
import { SearchPicker, type PickerGroup } from "./SearchPicker";

type SchemaTable = SourceSchema["tables"][number];

export const columnLabel = (column: PiiColumn) =>
  `${column.db_schema}.${column.table}.${column.column}`;

/** The four PII categories as a dropdown. */
export function CategorySelect({
  value,
  onChange,
  label = "Category",
  className,
}: {
  value: PiiCategory | "";
  onChange: (category: PiiCategory) => void;
  label?: string;
  className?: string;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as PiiCategory)}>
      <SelectTrigger aria-label={label} className={cn("w-44", className)}>
        <SelectValue placeholder="Choose a category" />
      </SelectTrigger>
      <SelectContent>
        {PII_CATEGORIES.map((category) => (
          <SelectItem key={category} value={category}>
            {CATEGORY_LABELS[category]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** A column's summary in a dialog: name, category and the rules that matched it. */
function ColumnSummary({ column, extra }: { column: PiiColumn; extra?: string }) {
  const rules = column.rules.map((r) => r.rule);
  if (column.manual) rules.push("added by hand");
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-surface p-3">
      <span className={cn(monoClasses, "break-all")}>{columnLabel(column)}</span>
      <p className="m-0 text-xs text-muted-foreground">
        {column.category ? CATEGORY_LABELS[column.category] : "No category"}
        {rules.length > 0 && (
          <>
            {" · "}
            <span className={monoClasses}>{rules.join(", ")}</span>
          </>
        )}
        {extra && ` · ${extra}`}
      </p>
    </div>
  );
}

/** Accept a rejected column as PII after all, picking its category. */
export function AcceptRejectedPiiDialog({
  target,
  onClose,
  workspaceId,
  systemId,
}: {
  target: PiiColumn | null;
  onClose: () => void;
  workspaceId: string;
  systemId: string;
}) {
  const decide = useDecidePiiColumn(workspaceId, systemId);
  const [category, setCategory] = useState<PiiCategory | "">("");
  const chosen = category || target?.category || "";
  const close = () => {
    decide.reset();
    setCategory("");
    onClose();
  };
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Accept as PII</DialogTitle>
          <DialogDescription>
            It moves from Rejected to PII with every rule that matched it, and stays
            protected: its values never reach the AI or DAWAM&apos;s storage.
          </DialogDescription>
        </DialogHeader>
        {target && <ColumnSummary column={target} />}
        <div className={labelClasses}>
          Category
          <CategorySelect value={chosen} onChange={setCategory} />
        </div>
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
            disabled={decide.isPending || !chosen}
            onClick={() =>
              target &&
              chosen &&
              decide.mutate(
                { columnId: target.id, action: "accept-rejected", category: chosen },
                {
                  onSuccess: () => {
                    toast.success("Accepted as PII.");
                    close();
                  },
                },
              )
            }
          >
            Accept as PII
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Add a column as PII by hand: table, then column (present columns only; those already PII
 * or suggested are not offered), then category.
 */
export function AddPiiColumnDialog({
  open,
  onOpenChange,
  workspaceId,
  systemId,
  tables,
  taken,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  systemId: string;
  tables: readonly SchemaTable[];
  /** Columns already PII: not offered. */
  taken: ReadonlySet<string>;
}) {
  const add = useAddPiiColumn(workspaceId, systemId);
  const suggested = usePiiColumns(workspaceId, systemId, "suggested", open);
  const [picker, setPicker] = useState<"table" | "column" | null>(null);
  const [tableId, setTableId] = useState("");
  const [columnId, setColumnId] = useState("");
  const [category, setCategory] = useState<PiiCategory | "">("");
  const waiting = new Set((suggested.data?.items ?? []).map((c) => c.id));
  const offered = (t: SchemaTable) =>
    presentColumns(t).filter((c) => !taken.has(c.id) && !waiting.has(c.id));
  const choosable = tables.filter((t) => t.status !== "source_removed");
  const table = choosable.find((t) => t.id === tableId);
  const column = table?.columns.find((c) => c.id === columnId);

  const close = (next: boolean) => {
    if (!next) {
      add.reset();
      setTableId("");
      setColumnId("");
      setCategory("");
      setPicker(null);
    }
    onOpenChange(next);
  };

  const tableGroups: PickerGroup[] = [];
  for (const t of choosable) {
    let group = tableGroups.find((g) => g.label === t.db_schema);
    if (!group) {
      group = { label: t.db_schema, items: [] };
      tableGroups.push(group);
    }
    group.items.push({ id: t.id, name: t.name, right: `${offered(t).length} columns` });
  }
  const columnGroups: PickerGroup[] = [
    {
      label: "Columns",
      items: (table ? offered(table) : []).map((c) => ({
        id: c.id,
        name: c.name,
        detail: c.data_type,
      })),
    },
  ];

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add PII column</DialogTitle>
          <DialogDescription>
            The column is protected at once: its values never reach the AI or DAWAM&apos;s
            storage, and its stored profile values are deleted.
          </DialogDescription>
        </DialogHeader>
        <div className={labelClasses}>
          Table
          <SearchPicker
            label="Table"
            placeholder="Choose a table"
            searchPlaceholder="Search tables"
            trigger={
              table && (
                <>
                  <Table2 aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
                  <span className={cn(monoClasses, "truncate")}>
                    <span className="text-subtle-foreground">{table.db_schema}.</span>
                    {table.name}
                  </span>
                </>
              )
            }
            groups={tableGroups}
            selectedId={tableId}
            open={picker === "table"}
            onOpenChange={(next) => setPicker(next ? "table" : null)}
            onSelect={(id) => {
              setTableId(id);
              setColumnId("");
              setPicker("column");
            }}
          />
        </div>
        <div className={labelClasses}>
          Column
          <SearchPicker
            label="Column"
            placeholder="Choose a column"
            searchPlaceholder="Search columns or types"
            disabled={!table}
            trigger={
              column && (
                <>
                  <span className={cn(monoClasses, "truncate")}>{column.name}</span>
                  <span className={cn(monoClasses, "text-xs text-subtle-foreground")}>
                    {column.data_type}
                  </span>
                </>
              )
            }
            groups={columnGroups}
            selectedId={columnId}
            open={picker === "column"}
            onOpenChange={(next) => setPicker(next ? "column" : null)}
            onSelect={setColumnId}
          />
        </div>
        <div className={labelClasses}>
          Category
          <CategorySelect value={category} onChange={setCategory} />
        </div>
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
            disabled={add.isPending || !column || !category}
            onClick={() =>
              column &&
              category &&
              add.mutate(
                { column_id: column.id, category },
                {
                  onSuccess: () => {
                    toast.success("Added as PII.");
                    close(false);
                  },
                },
              )
            }
          >
            Add PII column
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Remove a column's PII. Blocked while its staging mapping is hashed. When the table has
 * been profiled and the system has a live Connection, it offers to profile the table again
 * (ticked): the old profile was taken while the column was protected.
 */
export function RemovePiiDialog({
  target,
  onClose,
  workspaceId,
  systemId,
  canProfile,
  decidedBy,
}: {
  target: PiiColumn | null;
  onClose: () => void;
  workspaceId: string;
  systemId: string;
  /** A live Connection and the right to profile. */
  canProfile: boolean;
  decidedBy: string;
}) {
  const remove = useRemovePiiColumn(workspaceId, systemId);
  const [profileAgain, setProfileAgain] = useState(true);
  const handOnly = target != null && target.rules.length === 0;
  const offerProfile = canProfile && target?.table_profiled_at != null;
  const close = () => {
    remove.reset();
    setProfileAgain(true);
    onClose();
  };
  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && close()}>
      <DialogContent>
        <DialogHeader>
          <span className="grid size-9 place-items-center rounded-full bg-danger-subtle text-danger-strong">
            {target?.hashed_mapping ? (
              <Lock aria-hidden className="size-4" />
            ) : (
              <ShieldOff aria-hidden className="size-4" />
            )}
          </span>
          <DialogTitle>Remove PII</DialogTitle>
          <DialogDescription>
            {target?.hashed_mapping
              ? "This column is hashed in staging, so it stays PII for now."
              : "The column is no longer protected: its values can reach the AI and profiling."}
          </DialogDescription>
        </DialogHeader>
        {target && <ColumnSummary column={target} extra={decidedBy || undefined} />}
        {target?.hashed_mapping ? (
          <p className="m-0 text-sm text-muted-foreground">
            The staging mapping{" "}
            <span className={monoClasses}>{target.hashed_mapping}</span> hashes it. Switch it
            back to direct in Mappings first, then remove PII.
          </p>
        ) : (
          <>
            <ul className="m-0 flex list-disc flex-col gap-1 ps-5 text-sm text-muted-foreground">
              <li>
                {handOnly
                  ? "It leaves the PII list."
                  : "Moves to Rejected with every rule that matched it."}
              </li>
              <li>Its sensitive flag and category are cleared.</li>
            </ul>
            {offerProfile && target && (
              <label className="flex items-center gap-2 text-sm font-medium">
                <Checkbox
                  checked={profileAgain}
                  onCheckedChange={(checked) => setProfileAgain(checked === true)}
                />
                <span>
                  Profile <span className={monoClasses}>{target.table}</span> again
                  <span className="font-normal text-muted-foreground">
                    {" "}
                    (last profiled{" "}
                    {new Date(target.table_profiled_at!).toLocaleDateString()})
                  </span>
                </span>
              </label>
            )}
          </>
        )}
        {remove.isError && <ErrorText>{remove.error.message}</ErrorText>}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="ghost">
              {target?.hashed_mapping ? "Close" : "Cancel"}
            </Button>
          </DialogClose>
          {!target?.hashed_mapping && (
            <Button
              type="button"
              variant="destructive"
              disabled={remove.isPending}
              onClick={() =>
                target &&
                remove.mutate(
                  { columnId: target.id, profileAgain: offerProfile && profileAgain },
                  {
                    onSuccess: (result) => {
                      toast.success(
                        result.profile_job_id
                          ? "PII removed. Profiling the table again under Background jobs."
                          : "PII removed.",
                      );
                      close();
                    },
                  },
                )
              }
            >
              Remove PII
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The category of a column, as a badge. */
export function CategoryBadge({ category }: { category: PiiCategory | null }) {
  if (!category) {
    return <span className="text-subtle-foreground">—</span>;
  }
  return <Badge variant="pii">{CATEGORY_LABELS[category]}</Badge>;
}
