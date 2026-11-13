"use client";

import { Plus, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import type { Kpi, KpiTarget } from "../api/kpis";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { NativeSelect, labelClasses, monoClasses } from "./parts";

const AGGREGATIONS = [
  "Sum",
  "Average",
  "Count",
  "Count distinct",
  "Minimum",
  "Maximum",
  "Ratio",
  "Percentage",
  "Growth (period over period)",
];

const UNITS: readonly (readonly [string, readonly string[]])[] = [
  ["Currency", ["SAR", "USD", "EUR", "EGP"]],
  ["Percent and ratio", ["%", "Ratio", "Basis points"]],
  ["Count", ["Count", "Customers", "Accounts", "Transactions"]],
  ["Time", ["Minutes", "Hours", "Days"]],
];

const REFRESH_FREQUENCIES = [
  "Real time",
  "Hourly",
  "Daily",
  "Weekly",
  "Monthly",
  "Quarterly",
  "Yearly",
  "On demand",
];

/** The select value that shows the "type your own" input. */
const OTHER = "__other";

const sectionHeading = "m-0 text-xs font-medium tracking-wide text-subtle-foreground";
const hintClasses = "text-xs font-normal text-subtle-foreground";

/** The form's values, as the API takes them. */
export interface KpiFormValues {
  name: string;
  definition: string;
  formula_text: string;
  formula_sql: string | null;
  unit: string;
  aggregation: string;
  owner: string;
  refresh_frequency: string;
  targets: KpiTarget[];
}

/** The label above a control, with "(optional)" on the optional ones. */
function FieldLabel({
  htmlFor,
  optional = true,
  children,
}: {
  htmlFor: string;
  optional?: boolean;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className={labelClasses}>
      <span>
        {children}
        {optional && <span className="font-normal text-subtle-foreground"> (optional)</span>}
      </span>
    </label>
  );
}

/**
 * A dropdown of known values with an "Other…" choice that shows a text input under it. A
 * stored value that is not in the list opens with "Other…" selected and the value in the
 * input. Its two controls are read back by `readKpiFields` (`<name>_choice`, `<name>_other`).
 */
function PickField({
  name,
  label,
  options,
  groups,
  value,
}: {
  name: string;
  label: string;
  options?: readonly string[];
  groups?: typeof UNITS;
  value: string;
}) {
  const id = useId();
  const known = groups ? groups.flatMap(([, list]) => list) : (options ?? []);
  const [choice, setChoice] = useState(value === "" ? "" : known.includes(value) ? value : OTHER);
  const lower = label.toLowerCase();

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <NativeSelect
        id={id}
        name={`${name}_choice`}
        value={choice}
        onChange={(event) => setChoice(event.target.value)}
      >
        <option value="">Choose…</option>
        {groups
          ? groups.map(([group, list]) => (
              <optgroup key={group} label={group}>
                {list.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </optgroup>
            ))
          : options?.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
        <option value={OTHER}>Other…</option>
      </NativeSelect>
      {choice === OTHER && (
        <Input
          name={`${name}_other`}
          aria-label={`Other ${lower}`}
          placeholder={`Type the ${lower}`}
          maxLength={name === "refresh_frequency" ? 100 : 50}
          defaultValue={known.includes(value) ? "" : value}
          autoFocus={value === ""}
        />
      )}
    </div>
  );
}

let nextRowKey = 0;

/** Targets as label / value rows with a remove button; one empty row shows by default. */
function TargetRows({ initial }: { initial: readonly KpiTarget[] }) {
  const [rows, setRows] = useState(() =>
    (initial.length ? initial : [{ label: "", value: "" }]).map((t) => ({ ...t, key: nextRowKey++ })),
  );

  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => (
        <div key={row.key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_2rem] items-center gap-2">
          <Input
            name="target_label"
            aria-label="Target label"
            placeholder="Label, e.g. FY 2027"
            defaultValue={row.label}
          />
          <Input
            name="target_value"
            aria-label="Target value"
            placeholder="Value, e.g. 95"
            className="tabular-nums"
            defaultValue={row.value}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Remove target"
            onClick={() => setRows((all) => all.filter((r) => r.key !== row.key))}
          >
            <X aria-hidden />
          </Button>
        </div>
      ))}
      <div>
        <Button
          type="button"
          size="sm"
          onClick={() => setRows((all) => [...all, { label: "", value: "", key: nextRowKey++ }])}
        >
          <Plus aria-hidden />
          Add target
        </Button>
      </div>
    </div>
  );
}

/**
 * The Add / Edit KPI fields, in sections. `documentedUnder` is the Source System's name or
 * "Data Warehouse"; `owners` are the owners already in use, offered as suggestions;
 * `children` is extra content for the Ownership section (the Edit form's Status).
 */
export function KpiFields({
  initial,
  documentedUnder,
  owners,
  children,
}: {
  initial?: Kpi;
  documentedUnder: string;
  owners: readonly string[];
  children?: ReactNode;
}) {
  const ownerId = useId();
  const ownersId = useId();
  const sqlHintId = useId();
  const ownerHintId = useId();

  return (
    <div className="flex flex-col gap-5">
      <p className="m-0 flex items-center gap-2 text-sm text-muted-foreground">
        Documented under <Badge variant="neutral">{documentedUnder}</Badge>
      </p>

      <section className="flex flex-col gap-3">
        <h4 className={sectionHeading}>What it measures</h4>
        <label className={labelClasses}>
          Name
          <Input
            name="name"
            required
            maxLength={200}
            defaultValue={initial?.name}
            placeholder="e.g. Customer churn rate"
          />
        </label>
        <label className={labelClasses}>
          <span>
            Business definition
            <span className="font-normal text-subtle-foreground"> (optional)</span>
          </span>
          <Textarea
            name="definition"
            rows={3}
            maxLength={4000}
            defaultValue={initial?.definition}
            placeholder="What the number means to the business"
          />
        </label>
      </section>

      <section className="flex flex-col gap-3">
        <h4 className={sectionHeading}>How it is calculated</h4>
        <label className={labelClasses}>
          <span>
            Formula in words
            <span className="font-normal text-subtle-foreground"> (optional)</span>
          </span>
          <Textarea
            name="formula_text"
            rows={2}
            maxLength={4000}
            defaultValue={initial?.formula_text}
            placeholder="e.g. Customers lost ÷ customers at the start of the period"
          />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <PickField
            name="aggregation"
            label="Aggregation"
            options={AGGREGATIONS}
            value={initial?.aggregation ?? ""}
          />
          <PickField name="unit" label="Unit" groups={UNITS} value={initial?.unit ?? ""} />
        </div>
        <label className={labelClasses}>
          <span>
            Formula SQL
            <span className="font-normal text-subtle-foreground"> (optional)</span>
          </span>
          <Textarea
            name="formula_sql"
            rows={4}
            maxLength={20000}
            className={monoClasses}
            defaultValue={initial?.formula_sql ?? ""}
            placeholder="SELECT … FROM core.fact_…"
            aria-describedby={sqlHintId}
          />
          <span id={sqlHintId} className={hintClasses}>
            Written against the DW Schema, so it can wait until the Data Warehouse exists.
          </span>
        </label>
      </section>

      <section className="flex flex-col gap-3">
        <h4 className={sectionHeading}>Ownership</h4>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1.5">
            <FieldLabel htmlFor={ownerId}>Owner</FieldLabel>
            <Input
              id={ownerId}
              name="owner"
              list={ownersId}
              maxLength={200}
              defaultValue={initial?.owner}
              placeholder="Team or person"
              aria-describedby={ownerHintId}
            />
            <datalist id={ownersId}>
              {owners.map((owner) => (
                <option key={owner} value={owner} />
              ))}
            </datalist>
            <span id={ownerHintId} className={hintClasses}>
              Pick one already in use or type a new one.
            </span>
          </div>
          <PickField
            name="refresh_frequency"
            label="Refresh frequency"
            options={REFRESH_FREQUENCIES}
            value={initial?.refresh_frequency ?? ""}
          />
          {children}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h4 className={sectionHeading}>
          Targets <span className="font-normal"> (optional)</span>
        </h4>
        <TargetRows initial={initial?.targets ?? []} />
      </section>
    </div>
  );
}

/** The form's values, or the message for the first thing that is wrong. */
export function readKpiFields(data: FormData): KpiFormValues | string {
  const get = (name: string) => String(data.get(name) ?? "");

  const name = get("name").trim();
  if (!name) {
    return "Give the KPI a name.";
  }

  const labels = data.getAll("target_label").map(String);
  const values = data.getAll("target_value").map(String);
  const targets: KpiTarget[] = [];
  for (let i = 0; i < labels.length; i += 1) {
    const label = (labels[i] ?? "").trim();
    const value = (values[i] ?? "").trim();
    if (!label && !value) {
      continue;
    }
    if (!label || !value) {
      return "Each target needs both a label and a value.";
    }
    targets.push({ label, value });
  }

  const pick = (field: string): string | null => {
    const choice = get(`${field}_choice`);
    return choice === OTHER ? get(`${field}_other`).trim() || null : choice;
  };
  const aggregation = pick("aggregation");
  const unit = pick("unit");
  const refresh = pick("refresh_frequency");
  for (const [value, label] of [
    [aggregation, "aggregation"],
    [unit, "unit"],
    [refresh, "refresh frequency"],
  ] as const) {
    if (value === null) {
      return `Type the ${label}, or choose one from the list.`;
    }
  }

  return {
    name,
    definition: get("definition"),
    formula_text: get("formula_text"),
    formula_sql: get("formula_sql").trim() === "" ? null : get("formula_sql"),
    unit: unit ?? "",
    aggregation: aggregation ?? "",
    owner: get("owner").trim(),
    refresh_frequency: refresh ?? "",
    targets,
  };
}
