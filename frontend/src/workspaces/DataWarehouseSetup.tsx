"use client";

import { ChevronRight, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";

import {
  PLATFORM_LABELS,
  useDataWarehouse,
  useSetUpDataWarehouse,
  useUpdateDataWarehouse,
  type DataWarehouse,
  type TargetPlatform,
} from "../api/dataWarehouse";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Actions,
  Callout,
  ErrorText,
  Hint,
  NativeSelect,
  checkClasses,
  checkLabelClasses,
  fieldsetClasses,
  h3Classes,
  h4Classes,
  labelClasses,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

/** A settings section inside the dialog: the dialog is the card, so no border of its own. */
const dialogFormClasses = "flex max-w-xl flex-col gap-4";

const WEEKDAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
] as const;
const WEEKDAY_LABELS: Record<(typeof WEEKDAYS)[number], string> = {
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
  sunday: "Sun",
};
const LAYERS = ["staging", "core", "mart"] as const;
const LAYER_LABELS: Record<(typeof LAYERS)[number], string> = {
  staging: "Staging",
  core: "Core",
  mart: "Mart",
};
const PREFIXES = [
  ["dimension_prefix", "Dimension prefix", "dim_"],
  ["fact_prefix", "Fact prefix", "fact_"],
  ["bridge_prefix", "Bridge prefix", "bridge_"],
] as const;
const CASE_STYLES = [
  ["lower", "lower case"],
  ["upper", "UPPER CASE"],
] as const;
const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const PLATFORMS = Object.keys(PLATFORM_LABELS) as TargetPlatform[];

/** A section's heading, ruled off from the section above. */
const sectionHeadClasses =
  "flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border pt-4";
/** Short fields side by side; one per row on a narrow screen. */
const rowClasses = "grid gap-3 sm:grid-cols-3";
/** A choice drawn as a chip: its native input is hidden, the label shows the state. */
const choiceClasses =
  "cursor-pointer text-center text-sm font-medium text-muted-foreground transition-colors has-checked:text-foreground has-focus-visible:outline-2 has-focus-visible:outline-primary has-disabled:cursor-not-allowed has-disabled:opacity-60";
const summaryValueClasses = `${monoClasses} break-words whitespace-pre-wrap`;

/** What the collapsed sections hold, shown while they are closed. */
interface Summary {
  schemas: string;
  prefixes: string;
  dates: string;
}

function summarize(
  schemas: string[],
  prefixes: string[],
  startYear: string,
  endYear: string,
  weekend: string[],
): Summary {
  const days = WEEKDAYS.filter((day) => weekend.includes(day)).map(
    (day) => WEEKDAY_LABELS[day],
  );
  return {
    schemas: schemas.join("  "),
    prefixes: prefixes.join("  "),
    dates: `${startYear} to ${endYear}, weekend ${days.join(", ") || "none"}`,
  };
}

/**
 * The "Set up Data Warehouse" step (spec story 87): target platform, a schema name per
 * Layer, naming rules and date-dimension settings. Editors set it up and edit it later;
 * only owners may change the platform once it is set up.
 */
export function DataWarehouseSetup({ workspace }: { workspace: Workspace }) {
  const warehouse = useDataWarehouse(workspace.id);
  if (warehouse.isPending) {
    return <Loading className="p-0" />;
  }
  if (warehouse.isError) {
    return (
      <ErrorText>
        Could not load the Data Warehouse: {warehouse.error.message}
      </ErrorText>
    );
  }
  return <SetupForm workspace={workspace} warehouse={warehouse.data} />;
}

function SetupForm({
  workspace,
  warehouse,
}: {
  workspace: Workspace;
  warehouse: DataWarehouse;
}) {
  const setUp = useSetUpDataWarehouse(workspace.id);
  const update = useUpdateDataWarehouse(workspace.id);
  // Setting up flips `set_up`, which switches to the update call: report either.
  const mutation =
    [update, setUp].find((m) => m.isError || m.isSuccess || m.isPending) ??
    setUp;
  const canEdit = allows(workspace, "data_warehouse.set_up");
  const canChangePlatform =
    !warehouse.set_up || allows(workspace, "data_warehouse.change_platform");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (name: string) => String(data.get(name) ?? "");
    const settings = {
      layer_schemas: {
        staging: text("staging"),
        core: text("core"),
        mart: text("mart"),
      },
      naming_rules: {
        case_style: text("case_style") as "lower" | "upper",
        dimension_prefix: text("dimension_prefix"),
        fact_prefix: text("fact_prefix"),
        bridge_prefix: text("bridge_prefix"),
        // Not on this form: keep what is set (the audit columns of Staging Tables).
        load_ts_column: warehouse.naming_rules?.load_ts_column ?? "load_ts",
        source_system_column:
          warehouse.naming_rules?.source_system_column ?? "source_system",
      },
      date_dimension: {
        start_year: Number(text("start_year")),
        end_year: Number(text("end_year")),
        weekend_days: data
          .getAll("weekend_days")
          .map(String) as (typeof WEEKDAYS)[number][],
        include_hijri: data.get("include_hijri") === "on",
        fiscal_year_start_month: text("fiscal_year_start_month")
          ? Number(text("fiscal_year_start_month"))
          : null,
        include_time_dimension: data.get("include_time_dimension") === "on",
      },
    };
    // A disabled select is not submitted: the platform then stays as it is.
    const platform = (text("target_platform") ||
      warehouse.target_platform) as TargetPlatform;
    // The setup moves the page on, so the form may be gone when it succeeds: a promise
    // outlives it where the call's own callbacks would not. Errors show inline.
    const saved = warehouse.set_up
      ? update.mutateAsync({
          version: warehouse.version!,
          // Repeating the current platform is not a change, so editors may still save.
          ...(platform !== warehouse.target_platform ? { target_platform: platform } : {}),
          ...settings,
        })
      : setUp.mutateAsync({ target_platform: platform, ...settings });
    saved.then(
      () => toast.success("Saved."),
      () => undefined,
    );
  }

  const layers = warehouse.layer_schemas;
  const rules = warehouse.naming_rules;
  const dates = warehouse.date_dimension;
  const startYear = dates?.start_year ?? 2000;
  const endYear = dates?.end_year ?? 2040;
  const weekend: string[] = dates?.weekend_days ?? ["saturday", "sunday"];
  // The collapsed sections' values, re-read from the form each time they close.
  const [summary, setSummary] = useState<Summary>(() =>
    summarize(
      LAYERS.map((layer) => layers?.[layer] ?? layer),
      PREFIXES.map(([name, , fallback]) => rules?.[name] ?? fallback),
      String(startYear),
      String(endYear),
      weekend,
    ),
  );

  function onSectionsClosed(form: HTMLFormElement) {
    const data = new FormData(form);
    const text = (name: string) => String(data.get(name) ?? "");
    setSummary(
      summarize(
        LAYERS.map(text),
        PREFIXES.map(([name]) => text(name)),
        text("start_year"),
        text("end_year"),
        data.getAll("weekend_days").map(String),
      ),
    );
  }

  const sections = (
    <>
      <div className={sectionHeadClasses}>
        <h4 className={h4Classes}>Schema names</h4>
        <Hint>One schema or dataset per Layer.</Hint>
      </div>
      <div className={rowClasses}>
        {LAYERS.map((layer) => (
          <label key={layer} className={labelClasses}>
            {LAYER_LABELS[layer]}
            <Input
              className={monoClasses}
              name={layer}
              aria-label={`Schema or dataset name: ${layer}`}
              required
              defaultValue={layers?.[layer] ?? layer}
            />
          </label>
        ))}
      </div>
      <div className={sectionHeadClasses}>
        <h4 className={h4Classes}>Naming rules</h4>
      </div>
      <div className={labelClasses}>
        <span id="dw-case-style-label">Identifier case</span>
        <div
          role="radiogroup"
          aria-labelledby="dw-case-style-label"
          className="flex w-fit gap-0.5 rounded-md border border-control-edge p-0.5"
        >
          {CASE_STYLES.map(([value, label]) => (
            <label
              key={value}
              className={`${choiceClasses} rounded-sm px-3 py-1 has-checked:bg-muted`}
            >
              <input
                type="radio"
                className="sr-only"
                name="case_style"
                value={value}
                defaultChecked={(rules?.case_style ?? "lower") === value}
              />
              {label}
            </label>
          ))}
        </div>
      </div>
      <div className={rowClasses}>
        {PREFIXES.map(([name, label, fallback]) => (
          <label key={name} className={labelClasses}>
            {label}
            <Input
              className={monoClasses}
              name={name}
              defaultValue={rules?.[name] ?? fallback}
            />
          </label>
        ))}
      </div>
      <div className={sectionHeadClasses}>
        <h4 className={h4Classes}>Date dimension</h4>
      </div>
      <div className={rowClasses}>
        <label className={labelClasses}>
          First year
          <Input
            className="tabular-nums"
            name="start_year"
            type="number"
            aria-label="Date dimension: first year"
            required
            defaultValue={startYear}
          />
        </label>
        <label className={labelClasses}>
          Last year
          <Input
            className="tabular-nums"
            name="end_year"
            type="number"
            aria-label="Date dimension: last year"
            required
            defaultValue={endYear}
          />
        </label>
        <label className={labelClasses}>
          Fiscal year starts in
          <NativeSelect
            className="h-9"
            name="fiscal_year_start_month"
            defaultValue={String(dates?.fiscal_year_start_month ?? "")}
          >
            <option value="">No fiscal calendar</option>
            {MONTHS.map((month, i) => (
              <option key={month} value={String(i + 1)}>
                {month}
              </option>
            ))}
          </NativeSelect>
        </label>
      </div>
      <div className={labelClasses}>
        <span id="dw-weekend-label">Weekend days</span>
        <div
          role="group"
          aria-labelledby="dw-weekend-label"
          className="flex flex-wrap gap-1.5"
        >
          {WEEKDAYS.map((day) => (
            <label
              key={day}
              className={`${choiceClasses} min-w-12 rounded-md border border-control-edge px-2.5 py-1 has-checked:border-primary has-checked:bg-primary-subtle`}
            >
              <input
                type="checkbox"
                className="sr-only"
                name="weekend_days"
                value={day}
                aria-label={day}
                defaultChecked={weekend.includes(day)}
              />
              {WEEKDAY_LABELS[day]}
            </label>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <label className={checkLabelClasses}>
          <input
            type="checkbox"
            className={checkClasses}
            name="include_hijri"
            defaultChecked={dates?.include_hijri}
          />
          Include Hijri calendar attributes
        </label>
        <label className={checkLabelClasses}>
          <input
            type="checkbox"
            className={checkClasses}
            name="include_time_dimension"
            defaultChecked={dates?.include_time_dimension}
          />
          Include a time dimension
        </label>
      </div>
    </>
  );

  return (
    <form
      className={dialogFormClasses}
      onSubmit={onSubmit}
      aria-labelledby="dw-setup-title"
    >
      <h3 id="dw-setup-title" className={h3Classes}>
        {warehouse.set_up ? "Data Warehouse setup" : "Set up Data Warehouse"}
      </h3>
      {!warehouse.set_up && (
        <Hint>
          Choose where the warehouse will live. Until then, only this step and
          KPIs are available.
        </Hint>
      )}
      {!canEdit && <Hint>Only editors and owners can change the setup.</Hint>}
      <fieldset disabled={!canEdit} className={`${fieldsetClasses} gap-4`}>
        <label className={`${labelClasses} sm:max-w-xs`}>
          Target platform
          <NativeSelect
            className="h-9"
            name="target_platform"
            defaultValue={warehouse.target_platform ?? "postgresql"}
            disabled={!canEdit || !canChangePlatform}
          >
            {PLATFORMS.map((platform) => (
              <option key={platform} value={platform}>
                {PLATFORM_LABELS[platform]}
              </option>
            ))}
          </NativeSelect>
        </label>
        {!canChangePlatform && (
          <Hint>Only owners can change the platform after setup.</Hint>
        )}
        {warehouse.set_up && canEdit && canChangePlatform && (
          <Callout tone="warning" icon={<TriangleAlert aria-hidden />}>
            Once the Data Warehouse has tables or KPI formulas, a platform
            change goes through a Change Set.
          </Callout>
        )}
        {warehouse.set_up ? (
          sections
        ) : (
          <details
            className="group border-t border-border pt-4"
            onToggle={(event) => {
              const form = event.currentTarget.closest("form");
              if (canEdit && form && !event.currentTarget.open) {
                onSectionsClosed(form);
              }
            }}
          >
            <summary className="grid cursor-pointer list-none grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1.5 rounded-sm focus-visible:outline-2 focus-visible:outline-primary [&::-webkit-details-marker]:hidden">
              <ChevronRight
                aria-hidden
                className="size-4 text-subtle-foreground transition-transform group-open:rotate-90"
              />
              <span className={h4Classes}>Naming and dates</span>
              <span className="text-sm font-medium text-primary group-open:hidden">
                Review or change
              </span>
              <span className="col-span-2 col-start-2 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1 text-sm text-muted-foreground group-open:hidden">
                <span className="text-subtle-foreground">Schemas</span>
                <span className={summaryValueClasses}>{summary.schemas}</span>
                <span className="text-subtle-foreground">Prefixes</span>
                <span className={summaryValueClasses}>{summary.prefixes}</span>
                <span className="text-subtle-foreground">Date dimension</span>
                <span>{summary.dates}</span>
              </span>
            </summary>
            <div className="flex flex-col gap-4 pt-4">{sections}</div>
          </details>
        )}
      </fieldset>
      {mutation.isError && <ErrorText>{mutation.error.message}</ErrorText>}
      {canEdit && (
        <Actions>
          <Button type="submit" variant="primary" disabled={mutation.isPending}>
            {warehouse.set_up ? "Save" : "Set up Data Warehouse"}
          </Button>
        </Actions>
      )}
    </form>
  );
}
