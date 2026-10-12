"use client";

import { useLayerModel } from "../api/dwDiagram";
import { piiMarkLabel, usePiiMarks } from "../api/piiView";
import type { DwLayer } from "../api/dwModel";
import { h4Classes } from "../workspaces/parts";
import { BusMatrix } from "./BusMatrix";
import { StarDiagram } from "./StarDiagram";
import { linksOf } from "./starModel";
import { Loading } from "../shell/Loading";
import { ErrorText } from "../admin/parts";

/**
 * A Layer's star/snowflake diagram and bus matrix (stories 94, 95), under its model list.
 * Staging has no dimensional model, so it gets neither.
 */
export function StarPanel({ workspaceId, layer }: { workspaceId: string; layer: DwLayer }) {
  const model = useLayerModel(workspaceId, layer);
  const pii = usePiiMarks(workspaceId);

  if (model.isPending) {
    return <Loading label="Loading diagram…" className="p-0" />;
  }
  if (model.error) {
    return <ErrorText>Could not load the diagram: {model.error.message}</ErrorText>;
  }
  if (model.tables.length === 0) {
    return null;
  }
  const links = linksOf(model.tables);
  const marked = model.tables.flatMap((t) =>
    t.columns.filter((c) => pii.get(c.id)?.kind === "derived").map((c) => ({ t, c })),
  );
  const influenced = model.tables.filter((t) => pii.get(t.id)?.kind === "influenced");
  return (
    <>
      <section aria-labelledby="star-title" className="mt-6 flex flex-col gap-3">
        <h4 id="star-title" className={h4Classes}>Star diagram</h4>
        <StarDiagram tables={model.tables} pii={pii} />
        {/* The canvas is a picture: the same relationships, readable. */}
        <ul aria-label="Relationships" className="text-sm text-sand-11">
          {links.map((l) => (
            <li key={l.id}>
              {l.from.name} → {l.to.name} ({l.label})
            </li>
          ))}
        </ul>
        {(marked.length > 0 || influenced.length > 0) && (
          <ul aria-label="PII in the model" className="text-sm text-sand-11">
            {marked.map(({ t, c }) => (
              <li key={c.id}>
                {t.name}.{c.name} ({piiMarkLabel(pii.get(c.id)!)})
              </li>
            ))}
            {influenced.map((t) => (
              <li key={t.id}>{t.name} (PII-influenced)</li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="bus-title" className="mt-6 flex flex-col gap-3">
        <h4 id="bus-title" className={h4Classes}>Bus matrix</h4>
        <BusMatrix tables={model.tables} />
      </section>
    </>
  );
}
