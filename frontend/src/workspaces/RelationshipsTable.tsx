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
import { qualified, type ErEdge } from "./erGraph";
import { cardinality } from "./erLayout";
import { EmptyState, TableCard, linkButtonClasses, monoClasses } from "./parts";

type SchemaTable = SourceSchema["tables"][number];

/**
 * The relationships in effect (declared foreign keys, accepted inferred and manually added
 * ones) as a table: from → to, the joined columns, the cardinality and the kind. With
 * `onOpenTable` the table names are buttons; without it they are plain text. With `onDelete`
 * every row but a declared one has a delete button (a declared one shows a lock). `empty`
 * replaces the default message shown when there are no edges.
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
  const name = (id: string) => {
    const table = tables.get(id);
    const text = table ? qualified(table) : id;
    return onOpenTable ? (
      <button type="button" className={linkButtonClasses} onClick={() => onOpenTable(id)}>
        {text}
      </button>
    ) : (
      <span className={monoClasses}>{text}</span>
    );
  };
  return (
    <TableCard>
      <Table aria-label="Relationships">
        <TableHeader>
          <TableRow>
            <TableHead>From</TableHead>
            <TableHead>
              <span className="sr-only">References</span>
            </TableHead>
            <TableHead>To</TableHead>
            <TableHead>Columns</TableHead>
            <TableHead>Cardinality</TableHead>
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
              <TableCell>{name(edge.from)}</TableCell>
              <TableCell aria-hidden className="text-subtle-foreground">
                →
              </TableCell>
              <TableCell>{name(edge.to)}</TableCell>
              <TableCell mono className="text-mono">
                {edge.label}
              </TableCell>
              <TableCell>
                {cardinality(edge)}
                {edge.optional && (
                  <span className="text-subtle-foreground"> (optional)</span>
                )}
              </TableCell>
              <TableCell>
                {edge.kind === "declared" ? (
                  <Badge variant="fk">Declared</Badge>
                ) : edge.kind === "manual" ? (
                  <Badge>Manually added</Badge>
                ) : (
                  <Badge variant="mapped">Inferred, accepted</Badge>
                )}
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
