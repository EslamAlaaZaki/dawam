// The ER diagram's data (spec story 60): tables as nodes, relationships as edges, and the
// filters that narrow them. Pure, so the filters are tested without the canvas.
import type { Relationship } from "../api/relationships";
import type { SourceSchema } from "../api/snapshots";

type Table = SourceSchema["tables"][number];

export interface ErEdge {
  id: string;
  from: string;
  to: string;
  /** `declared` is a foreign key of the Snapshot; `inferred` an accepted inferred relationship. */
  kind: "declared" | "inferred";
  label: string;
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

/** Every relationship between present tables: declared foreign keys, then accepted inferred ones. */
export function relationshipEdges(
  tables: readonly Table[],
  accepted: readonly Relationship[],
): ErEdge[] {
  const ids = new Set(tables.filter(isPresent).map((t) => t.id));
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
        });
      }
    }
  }
  for (const rel of accepted) {
    if (rel.status === "accepted" && ids.has(rel.from_column.table_id) && ids.has(rel.to_column.table_id)) {
      edges.push({
        id: `inferred:${rel.id}`,
        from: rel.from_column.table_id,
        to: rel.to_column.table_id,
        kind: "inferred",
        label: `${rel.from_column.column} → ${rel.to_column.column}`,
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
