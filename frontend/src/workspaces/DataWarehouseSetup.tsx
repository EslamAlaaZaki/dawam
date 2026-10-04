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
    return <p>Loading…</p>;
  }
  if (warehouse.isError) {
    return <p role="alert">Could not load the Data Warehouse: {warehouse.error.message}</p>;
  }
  return <SetupForm key={String(warehouse.data.set_up)} workspace={workspace} warehouse={warehouse.data} />;
}

function SetupForm({ workspace, warehouse }: { workspace: Workspace; warehouse: DataWarehouse }) {
  const setUp = useSetUpDataWarehouse(workspace.id);
  const update = useUpdateDataWarehouse(workspace.id);
  const mutation = warehouse.set_up ? update : setUp;
  const canEdit = allows(workspace, "data_warehouse.set_up");
  const canChangePlatform = !warehouse.set_up || allows(workspace, "data_warehouse.change_platform");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (name: string) => String(data.get(name) ?? "");
    const settings = {
      layer_schemas: { staging: text("staging"), core: text("core"), mart: text("mart") },
      naming_rules: {
        case_style: text("case_style") as "lower" | "upper",
        dimension_prefix: text("dimension_prefix"),
        fact_prefix: text("fact_prefix"),
        bridge_prefix: text("bridge_prefix"),
      },
      date_dimension: {
        start_year: Number(text("start_year")),
        end_year: Number(text("end_year")),
        weekend_days: data.getAll("weekend_days").map(String) as (typeof WEEKDAYS)[number][],
        include_hijri: data.get("include_hijri") === "on",
        fiscal_year_start_month: text("fiscal_year_start_month")
          ? Number(text("fiscal_year_start_month"))
          : null,
        include_time_dimension: data.get("include_time_dimension") === "on",
      },
    };
    // A disabled select is not submitted: the platform then stays as it is.
    const platform = (text("target_platform") || warehouse.target_platform) as TargetPlatform;
    if (warehouse.set_up) {
      update.mutate({
        version: warehouse.version!,
        // Repeating the current platform is not a change, so editors may still save.
        ...(platform !== warehouse.target_platform ? { target_platform: platform } : {}),
        ...settings,
      });
    } else {
      setUp.mutate({ target_platform: platform, ...settings });
    }
  }

  const layers = warehouse.layer_schemas;
  const rules = warehouse.naming_rules;
  const dates = warehouse.date_dimension;
  return (
    <form className="form" onSubmit={onSubmit} aria-labelledby="dw-setup-title">
      <h3 id="dw-setup-title">{warehouse.set_up ? "Data Warehouse setup" : "Set up Data Warehouse"}</h3>
      {!warehouse.set_up && (
        <p>Choose where the warehouse will live. Until then, only this step and KPIs are available.</p>
      )}
      {!canEdit && <p>Only editors and owners can change the setup.</p>}
      <fieldset disabled={!canEdit}>
        <label>
          Target platform
          <select
            name="target_platform"
            defaultValue={warehouse.target_platform ?? "postgresql"}
            disabled={!canEdit || !canChangePlatform}
          >
            {PLATFORMS.map((platform) => (
              <option key={platform} value={platform}>
                {PLATFORM_LABELS[platform]}
              </option>
            ))}
          </select>
        </label>
        {!canChangePlatform && <p>Only owners can change the platform after setup.</p>}
        {LAYERS.map((layer) => (
          <label key={layer}>
            Schema or dataset name: {layer}
            <input name={layer} required defaultValue={layers?.[layer] ?? layer} />
          </label>
        ))}
        <label>
          Identifier case
          <select name="case_style" defaultValue={rules?.case_style ?? "lower"}>
            <option value="lower">lower case</option>
            <option value="upper">UPPER CASE</option>
          </select>
        </label>
        <label>
          Dimension prefix
          <input name="dimension_prefix" defaultValue={rules?.dimension_prefix ?? "dim_"} />
        </label>
        <label>
          Fact prefix
          <input name="fact_prefix" defaultValue={rules?.fact_prefix ?? "fact_"} />
        </label>
        <label>
          Bridge prefix
          <input name="bridge_prefix" defaultValue={rules?.bridge_prefix ?? "bridge_"} />
        </label>
        <label>
          Date dimension: first year
          <input name="start_year" type="number" required defaultValue={dates?.start_year ?? 2000} />
        </label>
        <label>
          Date dimension: last year
          <input name="end_year" type="number" required defaultValue={dates?.end_year ?? 2040} />
        </label>
        <div role="group" aria-label="Weekend days">
          {WEEKDAYS.map((day) => (
            <label key={day}>
              <input
                type="checkbox"
                name="weekend_days"
                value={day}
                defaultChecked={(dates?.weekend_days ?? ["saturday", "sunday"]).includes(day)}
              />
              {day}
            </label>
          ))}
        </div>
        <label>
          <input type="checkbox" name="include_hijri" defaultChecked={dates?.include_hijri} />
          Include Hijri calendar attributes
        </label>
        <label>
          Fiscal year starts in
          <select
            name="fiscal_year_start_month"
            defaultValue={String(dates?.fiscal_year_start_month ?? "")}
          >
            <option value="">No fiscal calendar</option>
            {Array.from({ length: 12 }, (_, i) => (
              <option key={i + 1} value={String(i + 1)}>
                Month {i + 1}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            name="include_time_dimension"
            defaultChecked={dates?.include_time_dimension}
          />
          Include a time dimension
        </label>
      </fieldset>
      {mutation.isError && (
        <p className="form-error" role="alert">
          {mutation.error.message}
        </p>
      )}
      {mutation.isSuccess && <p role="status">Saved.</p>}
      {canEdit && (
        <div className="form-actions">
          <button type="submit" disabled={mutation.isPending}>
            {warehouse.set_up ? "Save" : "Set up Data Warehouse"}
          </button>
        </div>
      )}
    </form>
  );
}
