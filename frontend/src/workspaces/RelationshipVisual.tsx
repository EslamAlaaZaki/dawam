"use client";

import type { Relationship } from "../api/relationships";
import type { SourceSchema } from "../api/snapshots";
import { Badge } from "../components/ui/badge";
import { cn } from "../lib/utils";
import { isUnique } from "./erGraph";
import { monoClasses } from "./parts";

type SchemaTable = SourceSchema["tables"][number];

/** What a column is in the relationship: the referencing column (FK) or the referenced one. */
export type EndTag = "FK" | "PK" | "UQ" | "COL";

/** One endpoint: a column (or, for a composite declared key, several) of a table. */
export interface Endpoint {
  tableId?: string;
  schema: string;
  table: string;
  columns: string;
  tag: EndTag;
  /** The column's data type, when known. */
  type?: string;
  /** The column is unique (shown on the referenced side). */
  unique?: boolean;
}

/** The tag of the referenced side: its primary key, a unique column, or a plain column. */
export function targetTag(table: SchemaTable | undefined, columns: readonly string[]): EndTag {
  if (!table || !isUnique(table, columns)) return "COL";
  const pk = table.columns.filter((c) => c.is_pk).map((c) => c.name);
  return pk.length === columns.length && columns.every((c) => pk.includes(c)) ? "PK" : "UQ";
}

/** Endpoints and cardinality of an API relationship, read against the Source Schema. */
export function relationshipEnds(
  relationship: Relationship,
  tables: ReadonlyMap<string, SchemaTable>,
): { from: Endpoint; to: Endpoint; cardinality: "N:1" | "1:1" } {
  const { from_column: f, to_column: t } = relationship;
  const fromTable = tables.get(f.table_id);
  return {
    from: {
      tableId: f.table_id,
      schema: f.db_schema,
      table: f.table,
      columns: f.column,
      tag: "FK",
      type: tables.get(f.table_id)?.columns.find((c) => c.name === f.column)?.data_type,
    },
    to: {
      type: tables.get(t.table_id)?.columns.find((c) => c.name === t.column)?.data_type,
      unique: tables.has(t.table_id) && isUnique(tables.get(t.table_id)!, [t.column]),
      tableId: t.table_id,
      schema: t.db_schema,
      table: t.table,
      columns: t.column,
      tag: targetTag(tables.get(t.table_id), [t.column]),
    },
    cardinality: fromTable && isUnique(fromTable, [f.column]) ? "1:1" : "N:1",
  };
}

/** The Badge variant for each key tag. */
export const tagVariant = { FK: "fk", PK: "pk", UQ: "neutral", COL: "neutral" } as const;

function Chip({
  end,
  struck,
  onOpenTable,
}: {
  end: Endpoint;
  struck: boolean;
  onOpenTable?: (id: string) => void;
}) {
  const name = `${end.schema}.${end.table}`;
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1">
      <Badge variant={tagVariant[end.tag]}>{end.tag}</Badge>
      {onOpenTable && end.tableId ? (
        <span className={cn(monoClasses, "break-all", struck && "line-through")}>
          <button
            type="button"
            className="cursor-pointer text-primary hover:underline"
            onClick={() => onOpenTable(end.tableId!)}
          >
            {name}
          </button>
          .{end.columns}
        </span>
      ) : (
        <span className={cn(monoClasses, "break-all", struck && "line-through")}>
          {name}.{end.columns}
        </span>
      )}
    </span>
  );
}

/**
 * The one way a relationship is drawn: the referencing column, an arrow labelled with the
 * cardinality (a solid line for a declared foreign key, a dashed one for an inferred or
 * manual relationship), and the referenced column. `muted` greys it (a rejected one) and
 * strikes the column names through.
 */
export function RelationshipVisual({
  from,
  to,
  cardinality,
  dashed = false,
  muted = false,
  onOpenTable,
  className,
}: {
  from: Endpoint;
  to: Endpoint;
  cardinality: string;
  dashed?: boolean;
  muted?: boolean;
  onOpenTable?: (id: string) => void;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex flex-wrap items-center gap-2 py-1",
        muted && "text-subtle-foreground",
        className,
      )}
    >
      <Chip end={from} struck={muted} onOpenTable={onOpenTable} />
      <span aria-hidden className="inline-flex flex-col items-center text-subtle-foreground">
        <span className="font-mono text-xs">{cardinality}</span>
        <span
          className={cn("block h-0 w-10 border-t border-subtle-foreground", dashed && "border-dashed")}
        />
      </span>
      <span className="sr-only">references ({cardinality})</span>
      <Chip end={to} struck={muted} onOpenTable={onOpenTable} />
    </span>
  );
}

function EndpointCard({ role, end }: { role: string; end: Endpoint }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1 rounded-lg border border-border bg-surface-raised p-3">
      <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        {role}
        <Badge variant={tagVariant[end.tag]}>{end.tag}</Badge>
      </span>
      <span className={cn(monoClasses, "break-all text-muted-foreground")}>
        {end.schema}.{end.table}
      </span>
      <span className={cn(monoClasses, "break-all font-semibold")}>{end.columns}</span>
      <span className={cn(monoClasses, "text-xs text-subtle-foreground")}>
        {end.type}
        {end.unique && " · unique"}
      </span>
    </div>
  );
}

/**
 * The two endpoints as separate cards (From and To) with a bridge between them: the
 * cardinality badge, an arrow and what it means. Stacked on narrow screens.
 */
export function RelationshipCards({
  from,
  to,
  cardinality,
}: {
  from: Endpoint;
  to: Endpoint;
  cardinality: string;
}) {
  return (
    <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
      <EndpointCard role="From" end={from} />
      <div className="flex shrink-0 flex-col items-center gap-0.5 text-xs text-subtle-foreground">
        <Badge>{cardinality}</Badge>
        <span aria-hidden className="text-base">
          <span className="sm:hidden">↓</span>
          <span className="hidden sm:inline">→</span>
        </span>
        <span>{cardinality === "1:1" ? "one to one" : "many to one"}</span>
      </div>
      <EndpointCard role="To" end={to} />
    </div>
  );
}
