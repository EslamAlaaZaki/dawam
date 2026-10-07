import type { DwTable } from "../api/dwModel";
import { busMatrix } from "./starModel";

/** Facts down, conformed dimensions across; a cell names the roles the dimension plays. */
export function BusMatrix({ tables }: { tables: DwTable[] }) {
  const { facts, dimensions, roles } = busMatrix(tables);
  if (facts.length === 0 || dimensions.length === 0) {
    return (
      <p className="empty-state">
        The bus matrix needs at least one fact and one conformed dimension.
      </p>
    );
  }
  return (
    <table className="admin-table" aria-label="Bus matrix">
      <thead>
        <tr>
          <th scope="col">Fact</th>
          {dimensions.map((d) => (
            <th key={d.id} scope="col">
              {d.name}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {facts.map((f) => (
          <tr key={f.id}>
            <th scope="row">{f.name}</th>
            {dimensions.map((d) => {
              const r = roles(f, d);
              return <td key={d.id}>{r.length ? `● ${r.join(", ")}` : ""}</td>;
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
