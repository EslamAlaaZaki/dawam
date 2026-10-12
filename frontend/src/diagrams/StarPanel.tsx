"use client";

import { useLayerModel } from "../api/dwDiagram";
import type { DwLayer } from "../api/dwModel";
import { h4Classes } from "../workspaces/parts";
import { BusMatrix } from "./BusMatrix";
import { StarDiagram } from "./StarDiagram";
import { linksOf } from "./starModel";

/**
 * A Layer's star/snowflake diagram and bus matrix (stories 94, 95), under its model list.
 * Staging has no dimensional model, so it gets neither.
 */
export function StarPanel({ workspaceId, layer }: { workspaceId: string; layer: DwLayer }) {
  const model = useLayerModel(workspaceId, layer);

  if (model.isPending) {
    return <p>Loading diagram…</p>;
  }
  if (model.error) {
    return <p role="alert">Could not load the diagram: {model.error.message}</p>;
  }
  if (model.tables.length === 0) {
    return null;
  }
  const links = linksOf(model.tables);
  return (
    <>
      <section aria-labelledby="star-title" className="mt-6 flex flex-col gap-3">
        <h4 id="star-title" className={h4Classes}>Star diagram</h4>
        <StarDiagram tables={model.tables} />
        {/* The canvas is a picture: the same relationships, readable. */}
        <ul aria-label="Relationships" className="text-sm text-sand-11">
          {links.map((l) => (
            <li key={l.id}>
              {l.from.name} → {l.to.name} ({l.label})
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="bus-title" className="mt-6 flex flex-col gap-3">
        <h4 id="bus-title" className={h4Classes}>Bus matrix</h4>
        <BusMatrix tables={model.tables} />
      </section>
    </>
  );
}
