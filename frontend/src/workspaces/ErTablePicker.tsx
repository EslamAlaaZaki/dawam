"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { Checkbox } from "../components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { cn } from "../lib/utils";
import { qualified } from "./erGraph";

interface PickerTable {
  id: string;
  db_schema: string;
  name: string;
}

const rowClasses =
  "flex min-h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-sm hover:bg-muted";

/** What the closed list says: every table, the one table, one whole schema, or a count. */
function summarize(groups: [string, PickerTable[]][], picked: PickerTable[]) {
  if (picked.length === 0) {
    return { text: "All tables", mono: false };
  }
  if (picked.length === 1) {
    return { text: qualified(picked[0]!), mono: true };
  }
  const ids = new Set(picked.map((t) => t.id));
  const whole = groups.filter(([, tables]) => tables.every((t) => ids.has(t.id)));
  if (whole.length === 1 && whole[0]![1].length === picked.length) {
    return { text: `${whole[0]![0]}, all ${picked.length} tables`, mono: true };
  }
  return { text: `${picked.length} tables`, mono: false };
}

/**
 * The ER diagram's one filter: the tables to show, grouped by Database Schema. Tick a
 * schema to take all its tables, or tick tables one by one, across schemas; nothing ticked
 * shows every table. The list stays open while ticking.
 */
export function ErTablePicker({
  tables,
  selected,
  onChange,
}: {
  /** The tables that can be shown, in the order to list them. */
  tables: readonly PickerTable[];
  /** The ticked table ids; empty shows every table. */
  selected: ReadonlySet<string>;
  onChange: (selected: ReadonlySet<string>) => void;
}) {
  const [folded, setFolded] = useState<ReadonlySet<string>>(() => new Set());
  const groups = useMemo(() => {
    const bySchema = new Map<string, PickerTable[]>();
    for (const table of tables) {
      bySchema.set(table.db_schema, [...(bySchema.get(table.db_schema) ?? []), table]);
    }
    return [...bySchema];
  }, [tables]);
  const summary = summarize(
    groups,
    tables.filter((t) => selected.has(t.id)),
  );

  const set = (ids: string[], on: boolean) => {
    const next = new Set(selected);
    for (const id of ids) {
      if (on) next.add(id);
      else next.delete(id);
    }
    onChange(next);
  };
  const fold = (schema: string) => {
    const next = new Set(folded);
    if (!next.delete(schema)) next.add(schema);
    setFolded(next);
  };

  return (
    <Popover>
      <PopoverTrigger
        aria-label={`Tables shown: ${summary.text}`}
        className={cn(
          "inline-flex h-8 w-64 max-w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-md border border-control-edge bg-background px-2.5 text-sm focus-visible:outline-2 focus-visible:outline-primary",
          selected.size > 0 && "border-primary bg-primary-subtle",
        )}
      >
        <span
          className={cn(
            "text-xs",
            selected.size > 0 ? "text-primary" : "text-subtle-foreground",
          )}
        >
          Tables
        </span>
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left font-medium",
            summary.mono && "font-mono text-mono",
          )}
        >
          {summary.text}
        </span>
        <ChevronDown aria-hidden className="size-3.5 shrink-0 text-subtle-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="flex max-h-80 flex-col overflow-auto p-1">
          <label className={cn(rowClasses, "font-medium")}>
            <Checkbox
              aria-label="All tables"
              checked={selected.size === 0}
              onCheckedChange={() => onChange(new Set())}
            />
            All tables
          </label>
          <div aria-hidden className="my-1 h-px shrink-0 bg-border" />
          {groups.map(([schema, schemaTables]) => {
            const ids = schemaTables.map((t) => t.id);
            const ticked = ids.filter((id) => selected.has(id)).length;
            const open = !folded.has(schema);
            return (
              <div key={schema} className="flex flex-col">
                <div className={cn(rowClasses, "cursor-default ps-0.5")}>
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-label={`${open ? "Collapse" : "Expand"} ${schema}`}
                    className="grid size-6 shrink-0 cursor-pointer place-items-center rounded-sm text-subtle-foreground focus-visible:outline-2 focus-visible:outline-primary"
                    onClick={() => fold(schema)}
                  >
                    <ChevronRight
                      aria-hidden
                      className={cn("size-3.5 transition-transform", open && "rotate-90")}
                    />
                  </button>
                  <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                    <Checkbox
                      aria-label={`All tables in ${schema}`}
                      checked={
                        ticked === ids.length ? true : ticked > 0 ? "indeterminate" : false
                      }
                      onCheckedChange={(checked) => set(ids, checked === true)}
                    />
                    <span className="min-w-0 truncate font-mono text-mono font-medium">
                      {schema}
                    </span>
                  </label>
                  <span className="text-xs text-subtle-foreground tabular-nums">
                    {ticked > 0 ? `${ticked} of ${ids.length}` : ids.length}
                  </span>
                </div>
                {open &&
                  schemaTables.map((table) => (
                    <label key={table.id} className={cn(rowClasses, "ps-9")}>
                      <Checkbox
                        aria-label={qualified(table)}
                        checked={selected.has(table.id)}
                        onCheckedChange={(checked) => set([table.id], checked === true)}
                      />
                      <span className="min-w-0 truncate font-mono text-mono">{table.name}</span>
                    </label>
                  ))}
              </div>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
