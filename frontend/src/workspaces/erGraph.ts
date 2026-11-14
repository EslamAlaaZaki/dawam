// The ER diagram's data (spec story 60): tables as nodes, relationships as edges, and the
// filters that narrow them. Pure, so the filters are tested without the canvas.
import type { Relationship } from "../api/relationships";
import type { SourceSchema } from "../api/snapshots";

type Table = SourceSchema["tables"][number];

export interface ErEdge {
  id: string;
  from: string;
  to: string;
  /**
   * `declared` is a foreign key of the Snapshot; `inferred` an accepted inferred
   * relationship (found by the rules, the AI or both); `manual` one a person added.
   */
  kind: "declared" | "inferred" | "manual";
  /** The relationship's id, for the ones that can be deleted (not `declared`). */
  relationshipId?: string;
  label: string;
  /** The referencing columns, in the `from` table. */
  fromCols: string[];
  /** The referenced columns, in the `to` table. */
  toCols: string[];
  /** A referencing column is nullable: the referenced side is "zero or one". */
  optional: boolean;
  /** The referencing columns are unique (or the primary key): one to one, not many to one. */
  oneToOne: boolean;
}

export interface ErFilters {
  /** A Database Schema name; "" keeps every one. */
  dbSchema: string;
  /** A table id to focus on, with its neighbours; "" keeps every table. */
  tableId: string;
}

export const qualified = (table: Pick<Table, "db_schema" | "name">) =>
  `${table.db_schema}.${table.name}`;

const isPresent = (table: Table) => !table.status || table.status === "present";

/** A table's columns the Snapshot still has. */
export const presentColumns = (table: Table) =>
  table.columns.filter((c) => !c.status || c.status === "present");

const sameSet = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((x) => b.includes(x));

/** The columns are the primary key, a unique constraint or a unique index of the table. */
export function isUnique(table: Table, columns: readonly string[]): boolean {
  if (columns.length === 0) return false;
  const pk = table.columns.filter((c) => c.is_pk).map((c) => c.name);
  return (
    sameSet(pk, columns) ||
    table.constraints.some(
      (c) => (c.type === "pk" || c.type === "unique") && sameSet(c.columns, columns),
    ) ||
    table.indexes.some((i) => i.is_unique && sameSet(i.columns, columns))
  );
}

const anyNullable = (table: Table, columns: readonly string[]) =>
  table.columns.some((c) => columns.includes(c.name) && c.is_nullable);

/** Every relationship between present tables: declared foreign keys, then accepted inferred and manual ones. */
export function relationshipEdges(
  tables: readonly Table[],
  accepted: readonly Relationship[],
): ErEdge[] {
  const ids = new Set(tables.filter(isPresent).map((t) => t.id));
  const byId = new Map(tables.map((t) => [t.id, t]));
  const edges: ErEdge[] = [];
  for (const table of tables.filter(isPresent)) {
    for (const constraint of table.constraints) {
      if (
        constraint.type === "fk" &&
        constraint.ref_table_id &&
        ids.has(constraint.ref_table_id)
      ) {
        edges.push({
          id: `fk:${table.id}:${constraint.name}`,
          from: table.id,
          to: constraint.ref_table_id,
          kind: "declared",
          label: `${constraint.columns.join(", ")} → ${constraint.ref_columns.join(", ")}`,
          fromCols: constraint.columns,
          toCols: constraint.ref_columns,
          optional: anyNullable(table, constraint.columns),
          oneToOne: isUnique(table, constraint.columns),
        });
      }
    }
  }
  for (const rel of accepted) {
    if (rel.status === "accepted" && ids.has(rel.from_column.table_id) && ids.has(rel.to_column.table_id)) {
      const from = byId.get(rel.from_column.table_id)!;
      const fromCols = [rel.from_column.column];
      edges.push({
        id: `inferred:${rel.id}`,
        relationshipId: rel.id,
        from: rel.from_column.table_id,
        to: rel.to_column.table_id,
        kind: rel.origin === "manual" ? "manual" : "inferred",
        label: `${rel.from_column.column} → ${rel.to_column.column}`,
        fromCols,
        toCols: [rel.to_column.column],
        optional: anyNullable(from, fromCols),
        oneToOne: isUnique(from, fromCols),
      });
    }
  }
  return edges;
}

/** The tables and edges left by the filters. A focused table keeps itself and its neighbours. */
export function filterGraph(
  tables: readonly Table[],
  edges: readonly ErEdge[],
  filters: ErFilters,
): { tables: Table[]; edges: ErEdge[] } {
  let keep = new Set(tables.filter(isPresent).map((t) => t.id));
  if (filters.dbSchema !== "") {
    const inSchema = new Set(
      tables.filter((t) => keep.has(t.id) && t.db_schema === filters.dbSchema).map((t) => t.id),
    );
    keep = inSchema;
  }
  if (filters.tableId !== "") {
    const near = new Set([filters.tableId]);
    for (const edge of edges) {
      if (edge.from === filters.tableId) near.add(edge.to);
      if (edge.to === filters.tableId) near.add(edge.from);
    }
    // The focus is not narrowed by the Database Schema filter: its neighbours may live elsewhere.
    keep = new Set(
      tables.filter(isPresent).map((t) => t.id).filter((id) => near.has(id)),
    );
  }
  return {
    tables: tables.filter((t) => keep.has(t.id)),
    edges: edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
  };
}

/** The columns a Keys-only card shows: the primary key and every column a relationship joins on. */
export function keyColumns(table: Table, edges: readonly ErEdge[]): Set<string> {
  const keys = new Set(table.columns.filter((c) => c.is_pk).map((c) => c.name));
  for (const edge of edges) {
    if (edge.from === table.id) edge.fromCols.forEach((c) => keys.add(c));
    if (edge.to === table.id) edge.toCols.forEach((c) => keys.add(c));
  }
  return keys;
}
