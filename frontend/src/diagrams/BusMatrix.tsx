import type { DwTable } from "../api/dwModel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { EmptyState } from "../workspaces/parts";
import { busMatrix } from "./starModel";

/** Facts down, conformed dimensions across; a cell names the roles the dimension plays. */
export function BusMatrix({ tables }: { tables: DwTable[] }) {
  const { facts, dimensions, roles } = busMatrix(tables);
  if (facts.length === 0 || dimensions.length === 0) {
    return (
      <EmptyState>The bus matrix needs at least one fact and one conformed dimension.</EmptyState>
    );
  }
  return (
    <div className="overflow-hidden rounded-xl border border-sand-5 bg-sand-1">
      <Table aria-label="Bus matrix">
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Fact</TableHead>
            {dimensions.map((d) => (
              <TableHead key={d.id} scope="col" className="font-mono text-mono">
                {d.name}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {facts.map((f) => (
            <TableRow key={f.id}>
              <TableHead scope="row" className="static bg-transparent font-mono text-mono text-sand-12">
                {f.name}
              </TableHead>
              {dimensions.map((d) => {
                const r = roles(f, d);
                return (
                  <TableCell key={d.id} className="text-jade-11">
                    {r.length ? `● ${r.join(", ")}` : ""}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
