// What only the Data Warehouse's KPIs page has: the "Documented under" column, the system
// filter, and the rule-based suggestions shown as rows next to the KPIs.
import type {
  Kpi,
  RejectedRuleSuggestion,
  RuleSuggestion,
} from "../api/kpis";
import type { SourceSystem } from "../api/systems";
import { Badge } from "../components/ui/badge";
import { NativeSelect } from "./parts";

/** A KPI row; a rule suggestion is not a stored KPI, so it carries its key and its links. */
export type KpiRow = Kpi & {
  /** Set on a rule suggestion: what accept and reject send (it has no KPI id). */
  ruleKey?: string;
  /** A rule suggestion's linked DW columns as `layer.table.column`. */
  ruleLinks?: string[];
};

export const isRuleSuggestion = (kpi: KpiRow) => kpi.ruleKey !== undefined;

/** A rule suggestion as a row of the Suggested tab, or (with `decided`) of the Rejected tab. */
export function ruleSuggestionRow(
  workspaceId: string,
  s: RuleSuggestion,
  decided?: Pick<RejectedRuleSuggestion, "decided_by" | "decided_at">,
): KpiRow {
  return {
    id: `rule:${s.key}`,
    ruleKey: s.key,
    ruleLinks: s.column_labels,
    workspace_id: workspaceId,
    source_system_id: null,
    name: s.name,
    definition: s.definition,
    formula_text: s.formula_text,
    formula_sql: s.formula_sql,
    unit: "",
    aggregation: s.aggregation,
    owner: "",
    refresh_frequency: "",
    targets: [],
    origin: "rule",
    // The rule that produced it, for the "Why suggested" column.
    rationale: s.formula_text,
    status: "draft",
    review_state: decided ? "rejected" : "suggested",
    decision: decided ? "rejected" : null,
    decided_by: decided?.decided_by ?? null,
    decided_at: decided?.decided_at ?? null,
    link_count: s.column_ids.length,
    feasibility: s.feasibility,
    version: 1,
    created_at: decided?.decided_at ?? "",
    updated_at: decided?.decided_at ?? "",
  };
}

/** The name of where a KPI is documented. */
export function documentedUnder(
  kpi: Pick<Kpi, "source_system_id">,
  systems: readonly SourceSystem[],
): string {
  if (kpi.source_system_id === null) return "Data Warehouse";
  return systems.find((s) => s.id === kpi.source_system_id)?.name ?? "Source System";
}

/** Neutral for a Source System, indigo for the Data Warehouse. */
export function UnderBadge({
  kpi,
  systems,
}: {
  kpi: Pick<Kpi, "source_system_id">;
  systems: readonly SourceSystem[];
}) {
  const name = documentedUnder(kpi, systems);
  return kpi.source_system_id === null ? (
    <Badge variant="accent">{name}</Badge>
  ) : (
    <Badge>{name}</Badge>
  );
}

/** "all", "dw" or a Source System's id. */
export type SystemFilter = string;

export const matchesSystem = (kpi: Pick<Kpi, "source_system_id">, filter: SystemFilter) =>
  filter === "all" || (filter === "dw" ? kpi.source_system_id === null : kpi.source_system_id === filter);

/** The toolbar's "Documented under" dropdown: All systems, Data Warehouse, each Source System. */
export function SystemFilterSelect({
  value,
  systems,
  onChange,
}: {
  value: SystemFilter;
  systems: readonly SourceSystem[];
  onChange: (value: SystemFilter) => void;
}) {
  return (
    <NativeSelect
      aria-label="Documented under"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-7 w-auto py-0 text-sm"
    >
      <option value="all">All systems</option>
      <option value="dw">Data Warehouse</option>
      {systems.map((s) => (
        <option key={s.id} value={s.id}>
          {s.name}
        </option>
      ))}
    </NativeSelect>
  );
}
