"use client";

import { Lock, Trash2 } from "lucide-react";

import type { SourceSchema } from "../api/snapshots";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { type ErEdge } from "./erGraph";
import { cardinality } from "./erLayout";
import { EmptyState, TableCard } from "./parts";
import { RelationshipVisual, targetTag } from "./RelationshipVisual";

type SchemaTable = SourceSchema["tables"][number];

/** The kind badge of a relationship in effect. */
export function KindBadge({ kind }: { kind: ErEdge["kind"] }) {
  return kind === "declared" ? (
    <Badge variant="fk">Declared</Badge>
  ) : kind === "manual" ? (
    <Badge>Manually added</Badge>
  ) : (
    <Badge variant="mapped">Inferred, accepted</Badge>
  );
}

/**
 * The relationships in effect (declared foreign keys, accepted inferred and manually added
 * ones) as a table: the relationship drawn as endpoints joined by an arrow with its
 * cardinality, and its kind. With `onOpenTable` the table names are buttons; without it they
 * are plain text. With `onDelete` every row but a declared one has a delete button (a
 * declared one shows a lock). `empty` replaces the default message shown when there are no
 * edges.
 */
export function RelationshipsTable({
  edges,
  tables,
  onOpenTable,
  onDelete,
  empty = "No relationships yet.",
}: {
  edges: readonly ErEdge[];
  tables: ReadonlyMap<string, SchemaTable>;
  onOpenTable?: (id: string) => void;
  onDelete?: (edge: ErEdge) => void;
  empty?: string;
}) {
  if (edges.length === 0) {
    return <EmptyState>{empty}</EmptyState>;
  }
  const end = (id: string, columns: readonly string[], tag: "FK" | "target") => {
    const table = tables.get(id);
    return {
      tableId: id,
      schema: table?.db_schema ?? "",
      table: table?.name ?? id,
      columns: columns.join(", "),
      tag: tag === "FK" ? ("FK" as const) : targetTag(table, columns),
    };
  };
  return (
    <TableCard>
      <Table aria-label="Relationships">
        <TableHeader>
          <TableRow>
            <TableHead>Relationship</TableHead>
            <TableHead>Kind</TableHead>
            {onDelete && (
              <TableHead>
                <span className="sr-only">Delete</span>
              </TableHead>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {edges.map((edge) => (
            <TableRow key={edge.id}>
              <TableCell className="whitespace-normal">
                <RelationshipVisual
                  from={end(edge.from, edge.fromCols, "FK")}
                  to={end(edge.to, edge.toCols, "target")}
                  cardinality={cardinality(edge)}
                  dashed={edge.kind !== "declared"}
                  onOpenTable={onOpenTable}
                />
                {edge.optional && (
                  <span className="ms-2 text-subtle-foreground">(optional)</span>
                )}
              </TableCell>
              <TableCell>
                <KindBadge kind={edge.kind} />
              </TableCell>
              {onDelete && (
                <TableCell className="text-end">
                  {edge.kind === "declared" ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span
                          role="img"
                          aria-label="Declared foreign key, can't be deleted"
                          className="inline-grid size-8 place-items-center text-subtle-foreground"
                        >
                          <Lock className="size-4" aria-hidden />
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        A foreign key in the source database. It can't be deleted here.
                      </TooltipContent>
                    </Tooltip>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label="Delete relationship"
                      onClick={() => onDelete(edge)}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  )}
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableCard>
  );
}
