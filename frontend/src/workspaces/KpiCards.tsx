// A KPI suggestion list in an Assistant answer, drawn as cards. The backend appends it as a
// fenced `dawam-kpis` block holding a JSON array; a block that does not parse (a reply still
// streaming, or a model's mistake) is shown as ordinary code instead.
import { Badge } from "../components/ui/badge";
import { cn } from "../lib/utils";
import { monoClasses } from "./parts";

export type KpiCard = {
  name: string;
  rationale: string;
  definition?: string;
  unit?: string;
};

const text = (value: unknown): value is string => typeof value === "string";

/** The cards in a block's JSON, or `null` when it is not a valid list. */
export function parseKpiCards(source: string): KpiCard[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return null;
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    return null;
  }
  const cards: KpiCard[] = [];
  for (const item of parsed as Record<string, unknown>[]) {
    if (item === null || typeof item !== "object" || !text(item.name) || !text(item.rationale)) {
      return null;
    }
    cards.push({
      name: item.name,
      rationale: item.rationale,
      definition: text(item.definition) ? item.definition : undefined,
      unit: text(item.unit) ? item.unit : undefined,
    });
  }
  return cards;
}

export function KpiCards({ cards }: { cards: KpiCard[] }) {
  return (
    <ul aria-label="Suggested KPIs" className="my-2 grid gap-2 sm:grid-cols-2">
      {cards.map((card, index) => (
        <li
          key={`${card.name}-${index}`}
          className="flex flex-col gap-1.5 rounded-md border border-border bg-surface-raised p-3"
        >
          <div className="flex items-start justify-between gap-2">
            <span className="font-semibold">{card.name}</span>
            {card.unit ? (
              <Badge variant="neutral" className={cn(monoClasses, "shrink-0")}>
                {card.unit}
              </Badge>
            ) : null}
          </div>
          {card.definition ? (
            <p className="text-sm text-foreground">{card.definition}</p>
          ) : null}
          <p className="text-sm text-muted-foreground">{card.rationale}</p>
        </li>
      ))}
    </ul>
  );
}
