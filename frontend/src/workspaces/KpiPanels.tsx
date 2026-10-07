"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  KPI_STATUS_LABELS,
  useCreateKpi,
  useDeleteKpi,
  useKpis,
  useUpdateKpi,
  type Kpi,
  type KpiScope,
  type KpiStatus,
  type KpiTarget,
} from "../api/kpis";
import { allows, type Workspace } from "../api/workspaces";

const STATUSES = Object.keys(KPI_STATUS_LABELS) as KpiStatus[];
const TARGETS_HINT = "One per line, as `label: value` (e.g. `FY2027: 95%`).";

/** Targets as editable text: `label: value` per line. */
function targetsToText(targets: readonly KpiTarget[]): string {
  return targets.map((t) => `${t.label}: ${t.value}`).join("\n");
}

/** The targets typed in, or an error message naming the line that is not `label: value`. */
function parseTargets(text: string): KpiTarget[] | string {
  const targets: KpiTarget[] = [];
  for (const line of text.split("\n")) {
    if (line.trim() === "") {
      continue;
    }
    const at = line.indexOf(":");
    const label = at < 0 ? "" : line.slice(0, at).trim();
    const value = at < 0 ? "" : line.slice(at + 1).trim();
    if (!label || !value) {
      return `Write each target as "label: value"; "${line.trim()}" is not.`;
    }
    targets.push({ label, value });
  }
  return targets;
}

interface Fields {
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

/** The form's fields, or an error message for the first one that is wrong. */
function readFields(form: HTMLFormElement): Fields | string {
  const data = new FormData(form);
  const get = (name: string) => String(data.get(name) ?? "");
  const targets = parseTargets(get("targets"));
  if (typeof targets === "string") {
    return targets;
  }
  return {
    name: get("name"),
    definition: get("definition"),
    formula_text: get("formula_text"),
    formula_sql: get("formula_sql").trim() === "" ? null : get("formula_sql"),
    unit: get("unit"),
    aggregation: get("aggregation"),
    owner: get("owner"),
    refresh_frequency: get("refresh_frequency"),
    targets,
  };
}

function KpiFields({ initial }: { initial?: Kpi }) {
  return (
    <>
      <label>
        Name
        <input name="name" required maxLength={200} defaultValue={initial?.name} />
      </label>
      <label>
        Business definition
        <textarea name="definition" rows={3} maxLength={4000} defaultValue={initial?.definition} />
      </label>
      <label>
        Formula in words
        <textarea
          name="formula_text"
          rows={2}
          maxLength={4000}
          defaultValue={initial?.formula_text}
        />
      </label>
      <label>
        Formula SQL
        <textarea
          name="formula_sql"
          rows={3}
          maxLength={20000}
          defaultValue={initial?.formula_sql ?? ""}
          aria-describedby="kpi-sql-hint"
        />
      </label>
      <p id="kpi-sql-hint" className="form-hint">
        Optional. Written against the DW Schema, so it can wait until the Data Warehouse exists.
      </p>
      <label>
        Unit
        <input name="unit" maxLength={50} defaultValue={initial?.unit} />
      </label>
      <label>
        Aggregation
        <input name="aggregation" maxLength={50} defaultValue={initial?.aggregation} />
      </label>
      <label>
        Owner
        <input name="owner" maxLength={200} defaultValue={initial?.owner} />
      </label>
      <label>
        Refresh frequency
        <input name="refresh_frequency" maxLength={100} defaultValue={initial?.refresh_frequency} />
      </label>
      <label>
        Targets
        <textarea
          name="targets"
          rows={3}
          defaultValue={targetsToText(initial?.targets ?? [])}
          aria-describedby="kpi-targets-hint"
        />
      </label>
      <p id="kpi-targets-hint" className="form-hint">
        {TARGETS_HINT}
      </p>
    </>
  );
}

/**
 * The KPIs folder of a Source System (`systemId`) or of the Data Warehouse (`systemId`
 * null): the KPIs documented there, one open at a time, and for owners and editors a form
 * to document another.
 */
export function KpiCatalog({
  workspace,
  scope,
  title,
}: {
  workspace: Workspace;
  scope: KpiScope;
  title: string;
}) {
  const kpis = useKpis(workspace.id, scope);
  const [openId, setOpenId] = useState<string | null>(null);

  if (kpis.isPending) {
    return <p>Loading…</p>;
  }
  if (kpis.isError) {
    return <p role="alert">Could not load the KPIs: {kpis.error.message}</p>;
  }
  const open = kpis.data.find((kpi) => kpi.id === openId);

  return (
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">{title}</h3>
      {kpis.data.length === 0 ? (
        <p className="empty-state">
          {scope.systemId
            ? "No KPIs yet. KPIs based on this system will be listed here."
            : "No KPIs yet. KPIs that span systems will be listed here."}
        </p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Status</th>
              <th scope="col">Unit</th>
              <th scope="col">Owner</th>
              <th scope="col">Refresh</th>
            </tr>
          </thead>
          <tbody>
            {kpis.data.map((kpi) => (
              <tr key={kpi.id}>
                <td>
                  <button type="button" className="secondary" onClick={() => setOpenId(kpi.id)}>
                    {kpi.name}
                  </button>
                  {kpi.origin === "ai" && <AiLabel />}
                </td>
                <td>{KPI_STATUS_LABELS[kpi.status]}</td>
                <td>{kpi.unit || "—"}</td>
                <td>{kpi.owner || "—"}</td>
                <td>{kpi.refresh_frequency || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && (
        <KpiDetails
          workspace={workspace}
          kpi={open}
          reload={() => kpis.refetch()}
          onClose={() => setOpenId(null)}
        />
      )}
      {allows(workspace, "kpi.edit") ? (
        <AddKpiForm workspaceId={workspace.id} scope={scope} onAdded={(kpi) => setOpenId(kpi.id)} />
      ) : (
        <p>Only owners and editors can document KPIs.</p>
      )}
    </section>
  );
}

/** Marks a KPI the AI suggested. */
function AiLabel() {
  return (
    <span className="badge" title="Suggested by the AI">
      {" "}
      AI-generated
    </span>
  );
}

/** Why the AI suggested a KPI. */
function AiRationale({ kpi }: { kpi: Kpi }) {
  if (kpi.origin !== "ai") {
    return null;
  }
  return (
    <p className="form-hint" aria-label="AI rationale">
      <AiLabel /> {kpi.rationale ?? "No rationale was given."}
    </p>
  );
}

function AddKpiForm({
  workspaceId,
  scope,
  onAdded,
}: {
  workspaceId: string;
  scope: KpiScope;
  onAdded: (kpi: Kpi) => void;
}) {
  const create = useCreateKpi(workspaceId);
  const [problem, setProblem] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = readFields(form);
    if (typeof fields === "string") {
      setProblem(fields);
      return;
    }
    setProblem(null);
    create.mutate(
      { ...fields, source_system_id: scope.systemId },
      {
        onSuccess: (kpi) => {
          form.reset();
          onAdded(kpi);
        },
      },
    );
  }

  return (
    <form className="form" onSubmit={onSubmit} aria-label="Add KPI">
      <h4>Document a KPI</h4>
      <KpiFields />
      {(problem || create.isError) && (
        <p className="form-error" role="alert">
          {problem ?? create.error?.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={create.isPending}>
          Add KPI
        </button>
      </div>
    </form>
  );
}

/** One KPI: editable (and deletable) by owners and editors, read-only for viewers. */
function KpiDetails({
  workspace,
  kpi,
  reload,
  onClose,
}: {
  workspace: Workspace;
  kpi: Kpi;
  reload: () => void;
  onClose: () => void;
}) {
  const update = useUpdateKpi(workspace.id, kpi.id);
  const remove = useDeleteKpi(workspace.id, kpi.id);
  const [problem, setProblem] = useState<string | null>(null);

  if (!allows(workspace, "kpi.edit")) {
    return (
      <section aria-label={`KPI ${kpi.name}`}>
        <h4>{kpi.name}</h4>
        <AiRationale kpi={kpi} />
        <dl className="details">
          <dt>Status</dt>
          <dd>{KPI_STATUS_LABELS[kpi.status]}</dd>
          <dt>Business definition</dt>
          <dd>{kpi.definition || "—"}</dd>
          <dt>Formula in words</dt>
          <dd>{kpi.formula_text || "—"}</dd>
          <dt>Formula SQL</dt>
          <dd>{kpi.formula_sql ? <code>{kpi.formula_sql}</code> : "—"}</dd>
          <dt>Unit</dt>
          <dd>{kpi.unit || "—"}</dd>
          <dt>Aggregation</dt>
          <dd>{kpi.aggregation || "—"}</dd>
          <dt>Owner</dt>
          <dd>{kpi.owner || "—"}</dd>
          <dt>Refresh frequency</dt>
          <dd>{kpi.refresh_frequency || "—"}</dd>
          <dt>Targets</dt>
          <dd>{kpi.targets.length ? targetsToText(kpi.targets) : "—"}</dd>
        </dl>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </section>
    );
  }

  const versionConflict = update.error instanceof ApiError && update.error.code === "version_conflict";

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = readFields(form);
    if (typeof fields === "string") {
      setProblem(fields);
      return;
    }
    setProblem(null);
    const status = String(new FormData(form).get("status")) as KpiStatus;
    update.mutate({ version: kpi.version, ...fields, status });
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form
      key={`${kpi.id}-${kpi.version}`}
      className="form"
      onSubmit={onSubmit}
      aria-label={`KPI ${kpi.name}`}
    >
      <h4>{kpi.name}</h4>
      <AiRationale kpi={kpi} />
      <label>
        Status
        <select name="status" defaultValue={kpi.status}>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {KPI_STATUS_LABELS[status]}
            </option>
          ))}
        </select>
      </label>
      <KpiFields initial={kpi} />
      {versionConflict ? (
        <div className="form-error" role="alert">
          <p>
            Someone else changed this KPI since you opened it. Reload it to see their changes,
            then edit again.
          </p>
          <button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </button>
        </div>
      ) : (
        (problem || update.isError || remove.isError) && (
          <p className="form-error" role="alert">
            {problem ?? update.error?.message ?? remove.error?.message}
          </p>
        )
      )}
      {update.isSuccess && <p role="status">Saved.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
        <button
          type="button"
          className="secondary"
          disabled={remove.isPending}
          onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
        >
          Delete KPI
        </button>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </form>
  );
}
