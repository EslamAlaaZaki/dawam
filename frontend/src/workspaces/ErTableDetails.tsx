"use client";

import { ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import type { SourceSchema } from "../api/snapshots";
import { Badge } from "../components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { cardinality } from "./erLayout";
import { isUnique, presentColumns, qualified, type ErEdge } from "./erGraph";
import { Hint, TableCard, h5Classes, linkButtonClasses } from "./parts";

type SourceTable = SourceSchema["tables"][number];

const rows = (n: number | null) =>
  n == null
    ? null
    : n >= 1e6
      ? `${(n / 1e6).toFixed(1)}M`
      : n >= 1e3
        ? `${(n / 1e3).toFixed(1)}k`
        : String(n);

const KindBadge = ({ edge }: { edge: ErEdge }) =>
  edge.kind === "declared" ? (
    <Badge variant="fk">Declared</Badge>
  ) : edge.kind === "manual" ? (
    <Badge>Manually added</Badge>
  ) : (
    <Badge variant="mapped">Inferred, accepted</Badge>
  );

function Section({ title, empty, children }: { title: string; empty: boolean; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className={h5Classes}>{title}</h4>
      {empty ? <Hint>None.</Hint> : <TableCard>{children}</TableCard>}
    </section>
  );
}

/**
 * A table's details from the ER diagram: its columns (keys, uniqueness, indexes, PII,
 * defaults, descriptions), its relationships in and out, its constraints and indexes. A
 * related table's name opens that table's details.
 */
export function ErTableDetails({
  table,
  tables,
  edges,
  onClose,
  onOpenTable,
}: {
  table: SourceTable | null;
  /** Every present table, by id. */
  tables: ReadonlyMap<string, SourceTable>;
  /** Every relationship, whatever the filters. */
  edges: readonly ErEdge[];
  onClose: () => void;
  onOpenTable: (id: string) => void;
}) {
  return (
    <Dialog open={table !== null} onOpenChange={(open) => !open && onClose()}>
      {table && (
        <DialogContent
          aria-describedby={undefined}
          className="w-[min(1040px,calc(100%-2rem))] gap-4"
        >
          <Details table={table} tables={tables} edges={edges} onOpenTable={onOpenTable} />
        </DialogContent>
      )}
    </Dialog>
  );
}

function Details({
  table,
  tables,
  edges,
  onOpenTable,
}: {
  table: SourceTable;
  tables: ReadonlyMap<string, SourceTable>;
  edges: readonly ErEdge[];
  onOpenTable: (id: string) => void;
}) {
  const columns = presentColumns(table);
  const out = edges.filter((e) => e.from === table.id);
  const incoming = edges.filter((e) => e.to === table.id);
  const indexed = new Set(table.indexes.flatMap((i) => i.columns));
  const protectedCount = columns.filter((c) => c.is_protected).length;
  const pk = columns.filter((c) => c.is_pk).map((c) => c.name);
  const estimate = rows(table.row_estimate);
  const description = table.description ?? table.comment;
  const link = (id: string) => {
    const target = tables.get(id);
    return target ? (
      <button type="button" className={linkButtonClasses} onClick={() => onOpenTable(id)}>
        {qualified(target)}
      </button>
    ) : null;
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-mono break-all">{qualified(table)}</DialogTitle>
        <div className="flex flex-wrap items-center gap-2 text-sm text-subtle-foreground">
          <Badge>{table.kind === "view" ? "View" : "Table"}</Badge>
          <span>
            {columns.length} {columns.length === 1 ? "column" : "columns"}
          </span>
          {estimate && <span>about {estimate} rows</span>}
          <span>
            PK <span className="font-mono text-mono">({pk.length ? pk.join(", ") : "none"})</span>
          </span>
          {protectedCount > 0 && (
            <Badge variant="pii">
              <ShieldAlert aria-hidden />
              {protectedCount} Protected {protectedCount === 1 ? "Column" : "Columns"}
            </Badge>
          )}
        </div>
      </DialogHeader>
      <p className={description ? "text-muted-foreground" : "text-subtle-foreground"}>
        {description || "No description yet."}
      </p>

      <Section title="Columns" empty={columns.length === 0}>
        <Table aria-label="Columns">
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Column</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Nullable</TableHead>
              <TableHead>Keys</TableHead>
              <TableHead>Default</TableHead>
              <TableHead>Description</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {columns.map((column) => (
              <TableRow key={column.id}>
                <TableCell className="text-subtle-foreground">{column.ordinal}</TableCell>
                <TableCell mono className="font-medium">
                  {column.name}
                </TableCell>
                <TableCell mono>{column.data_type}</TableCell>
                <TableCell>
                  {column.is_nullable ? (
                    <span className="text-subtle-foreground">YES</span>
                  ) : (
                    <span className="font-medium">NO</span>
                  )}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-1">
                    {column.is_pk && <Badge variant="pk">PK</Badge>}
                    {out
                      .filter((e) => e.fromCols.includes(column.name))
                      .map((e) => (
                        <span key={e.id} className="inline-flex items-center gap-1">
                          <Badge variant={e.kind === "declared" ? "fk" : "mapped"}>
                            {e.kind === "declared"
                              ? "FK"
                              : e.kind === "manual"
                                ? "FK manual"
                                : "FK inferred"}
                          </Badge>
                          <span className="font-mono text-mono">
                            → {link(e.to)}.{e.toCols.join(", ")}
                          </span>
                        </span>
                      ))}
                    {!column.is_pk && isUnique(table, [column.name]) && <Badge>UQ</Badge>}
                    {indexed.has(column.name) && <Badge title="Part of an index">IX</Badge>}
                    {column.is_protected && (
                      <Badge variant="pii">
                        <ShieldAlert aria-hidden />
                        {column.pii_category?.replace("_", " ") ?? "Protected"}
                      </Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell mono>
                  {column.default ?? <span className="font-sans text-subtle-foreground">none</span>}
                </TableCell>
                <TableCell className="min-w-48 whitespace-normal text-muted-foreground">
                  {column.description ?? column.comment ?? ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>

      <Section title="Relationships" empty={out.length + incoming.length === 0}>
        <Table aria-label="Table relationships">
          <TableHeader>
            <TableRow>
              <TableHead>Cardinality</TableHead>
              <TableHead>Columns</TableHead>
              <TableHead>Table</TableHead>
              <TableHead>Kind</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {out.map((e) => (
              <TableRow key={`out:${e.id}`}>
                <TableCell>{cardinality(e)}</TableCell>
                <TableCell mono>{e.fromCols.join(", ")}</TableCell>
                <TableCell>
                  → {link(e.to)}{" "}
                  <span className="font-mono text-mono text-subtle-foreground">
                    ({e.toCols.join(", ")})
                  </span>
                </TableCell>
                <TableCell>
                  <KindBadge edge={e} />
                </TableCell>
              </TableRow>
            ))}
            {incoming.map((e) => (
              <TableRow key={`in:${e.id}`}>
                <TableCell>{cardinality(e, true)}</TableCell>
                <TableCell mono>{e.toCols.join(", ")}</TableCell>
                <TableCell>
                  ← {link(e.from)}{" "}
                  <span className="font-mono text-mono text-subtle-foreground">
                    ({e.fromCols.join(", ")})
                  </span>
                </TableCell>
                <TableCell>
                  <KindBadge edge={e} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>

      <Section title="Constraints" empty={table.constraints.length === 0}>
        <Table aria-label="Constraints">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Columns</TableHead>
              <TableHead>References</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.constraints.map((c) => (
              <TableRow key={c.name}>
                <TableCell mono>{c.name}</TableCell>
                <TableCell>{c.type.toUpperCase()}</TableCell>
                <TableCell mono>{c.columns.join(", ")}</TableCell>
                <TableCell mono>
                  {c.type === "fk" && c.ref_table
                    ? `→ ${c.ref_db_schema}.${c.ref_table} (${c.ref_columns.join(", ")})`
                    : ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>

      <Section title="Indexes" empty={table.indexes.length === 0}>
        <Table aria-label="Indexes">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Unique</TableHead>
              <TableHead>Columns</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {table.indexes.map((i) => (
              <TableRow key={i.name}>
                <TableCell mono>{i.name}</TableCell>
                <TableCell>{i.is_unique ? "YES" : "NO"}</TableCell>
                <TableCell mono>{i.columns.join(", ")}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </>
  );
}
