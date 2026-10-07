import type { DwTable } from "../api/dwModel";

export type Link = {
  /** The id of the foreign key column. */
  id: string;
  from: DwTable;
  to: DwTable;
  /** The role the dimension plays for the referencing table, else the column name. */
  label: string;
};

/** Foreign keys between the given tables (those pointing outside them are left out). */
export function linksOf(tables: DwTable[]): Link[] {
  const byId = new Map(tables.map((t) => [t.id, t]));
  return tables.flatMap((from) =>
    from.columns.flatMap((c) => {
      const to = c.references_table_id ? byId.get(c.references_table_id) : undefined;
      return to ? [{ id: c.id, from, to, label: c.role_name ?? c.name }] : [];
    }),
  );
}

export const isFact = (t: DwTable) => t.kind === "fact";

/** Facts, then the dimensions one hop out, then those two hops out (a snowflake). */
export function ranks(tables: DwTable[], links: Link[]): Map<string, number> {
  const rank = new Map<string, number>();
  let frontier = tables.filter(isFact);
  frontier.forEach((t) => rank.set(t.id, 0));
  for (let depth = 1; frontier.length > 0; depth++) {
    const next: DwTable[] = [];
    for (const link of links) {
      if (frontier.includes(link.from) && !rank.has(link.to.id)) {
        rank.set(link.to.id, depth);
        next.push(link.to);
      }
    }
    frontier = next;
  }
  const last = Math.max(0, ...rank.values()) + 1;
  tables.forEach((t) => !rank.has(t.id) && rank.set(t.id, last));
  return rank;
}

/** Facts and conformed dimensions, and the roles a dimension plays for a fact. */
export function busMatrix(tables: DwTable[]) {
  const byName = (a: DwTable, b: DwTable) => a.name.localeCompare(b.name);
  const facts = tables.filter(isFact).sort(byName);
  const dimensions = tables.filter((t) => t.kind === "dimension" && t.is_conformed).sort(byName);
  const links = linksOf(tables);
  const roles = (fact: DwTable, dimension: DwTable) =>
    links.filter((l) => l.from === fact && l.to === dimension).map((l) => l.label);
  return { facts, dimensions, roles };
}
