"use client";

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
  ErrorText,
  Hint,
  NativeSelect,
  checkClasses,
  checkLabelClasses,
  fieldsetClasses,
  h3Classes,
  h4Classes,
  labelClasses,
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
const LAYERS = ["staging", "core", "mart"] as const;
const PLATFORMS = Object.keys(PLATFORM_LABELS) as TargetPlatform[];

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
        <label className={labelClasses}>
          Target platform
          <NativeSelect
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
        <h4 className={`${h4Classes} border-t border-border pt-4`}>
          Schema names
        </h4>
        {LAYERS.map((layer) => (
          <label key={layer} className={labelClasses}>
            Schema or dataset name: {layer}
            <Input
              className="font-mono text-mono"
              name={layer}
              required
              defaultValue={layers?.[layer] ?? layer}
            />
          </label>
        ))}
        <h4 className={`${h4Classes} border-t border-border pt-4`}>
          Naming rules
        </h4>
        <label className={labelClasses}>
          Identifier case
          <NativeSelect
            name="case_style"
            defaultValue={rules?.case_style ?? "lower"}
          >
            <option value="lower">lower case</option>
            <option value="upper">UPPER CASE</option>
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Dimension prefix
          <Input
            className="font-mono text-mono"
            name="dimension_prefix"
            defaultValue={rules?.dimension_prefix ?? "dim_"}
          />
        </label>
        <label className={labelClasses}>
          Fact prefix
          <Input
            className="font-mono text-mono"
            name="fact_prefix"
            defaultValue={rules?.fact_prefix ?? "fact_"}
          />
        </label>
        <label className={labelClasses}>
          Bridge prefix
          <Input
            className="font-mono text-mono"
            name="bridge_prefix"
            defaultValue={rules?.bridge_prefix ?? "bridge_"}
          />
        </label>
        <h4 className={`${h4Classes} border-t border-border pt-4`}>
          Date dimension
        </h4>
        <label className={labelClasses}>
          Date dimension: first year
          <Input
            name="start_year"
            type="number"
            required
            defaultValue={dates?.start_year ?? 2000}
          />
        </label>
        <label className={labelClasses}>
          Date dimension: last year
          <Input
            name="end_year"
            type="number"
            required
            defaultValue={dates?.end_year ?? 2040}
          />
        </label>
        <div
          role="group"
          aria-label="Weekend days"
          className="flex flex-wrap gap-x-4 gap-y-2"
        >
          {WEEKDAYS.map((day) => (
            <label key={day} className={checkLabelClasses}>
              <input
                type="checkbox"
                className={checkClasses}
                name="weekend_days"
                value={day}
                defaultChecked={(
                  dates?.weekend_days ?? ["saturday", "sunday"]
                ).includes(day)}
              />
              {day}
            </label>
          ))}
        </div>
        <label className={checkLabelClasses}>
          <input
            type="checkbox"
            className={checkClasses}
            name="include_hijri"
            defaultChecked={dates?.include_hijri}
          />
          Include Hijri calendar attributes
        </label>
        <label className={labelClasses}>
          Fiscal year starts in
          <NativeSelect
            name="fiscal_year_start_month"
            defaultValue={String(dates?.fiscal_year_start_month ?? "")}
          >
            <option value="">No fiscal calendar</option>
            {Array.from({ length: 12 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>
                Month {i + 1}
              </option>
            ))}
          </NativeSelect>
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
