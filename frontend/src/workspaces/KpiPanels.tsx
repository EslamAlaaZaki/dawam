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
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Textarea } from "../components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  Actions,
  DetailList,
  EmptyState,
  ErrorText,
  Hint,
  NativeSelect,
  TableCard,
  formCardClasses,
  h3Classes,
  h4Classes,
  labelClasses,
  linkButtonClasses,
  monoClasses,
} from "./parts";

const STATUS_VARIANTS = { draft: "neutral", in_review: "warning", approved: "success" } as const;

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
      <label className={labelClasses}>
        Name
        <Input name="name" required maxLength={200} defaultValue={initial?.name} />
      </label>
      <label className={labelClasses}>
        Business definition
        <Textarea name="definition" rows={3} maxLength={4000} defaultValue={initial?.definition} />
      </label>
      <label className={labelClasses}>
        Formula in words
        <Textarea
          name="formula_text"
          rows={2}
          maxLength={4000}
          defaultValue={initial?.formula_text}
        />
      </label>
      <label className={labelClasses}>
        Formula SQL
        <Textarea
          name="formula_sql"
          rows={3}
          maxLength={20000}
          defaultValue={initial?.formula_sql ?? ""}
          aria-describedby="kpi-sql-hint"
        />
      </label>
      <Hint id="kpi-sql-hint">
        Optional. Written against the DW Schema, so it can wait until the Data Warehouse exists.
      </Hint>
      <label className={labelClasses}>
        Unit
        <Input name="unit" maxLength={50} defaultValue={initial?.unit} />
      </label>
      <label className={labelClasses}>
        Aggregation
        <Input name="aggregation" maxLength={50} defaultValue={initial?.aggregation} />
      </label>
      <label className={labelClasses}>
        Owner
        <Input name="owner" maxLength={200} defaultValue={initial?.owner} />
      </label>
      <label className={labelClasses}>
        Refresh frequency
        <Input name="refresh_frequency" maxLength={100} defaultValue={initial?.refresh_frequency} />
      </label>
      <label className={labelClasses}>
        Targets
        <Textarea
          name="targets"
          rows={3}
          defaultValue={targetsToText(initial?.targets ?? [])}
          aria-describedby="kpi-targets-hint"
        />
      </label>
      <Hint id="kpi-targets-hint">
        {TARGETS_HINT}
      </Hint>
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
    return <Loading />;
  }
  if (kpis.isError) {
    return <ErrorText>Could not load the KPIs: {kpis.error.message}</ErrorText>;
  }
  const open = kpis.data.find((kpi) => kpi.id === openId);

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <h3 id="folder-title" className={h3Classes}>
        {title}
      </h3>
      {kpis.data.length === 0 ? (
        <EmptyState>
          {scope.systemId
            ? "No KPIs yet. KPIs based on this system will be listed here."
            : "No KPIs yet. KPIs that span systems will be listed here."}
        </EmptyState>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col">Status</TableHead>
                <TableHead scope="col">Unit</TableHead>
                <TableHead scope="col">Owner</TableHead>
                <TableHead scope="col">Refresh</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {kpis.data.map((kpi) => (
                <TableRow key={kpi.id}>
                  <TableCell>
                    <button
                      type="button"
                      className={linkButtonClasses}
                      onClick={() => setOpenId(kpi.id)}
                    >
                      {kpi.name}
                    </button>
                    {kpi.origin === "ai" && <AiLabel />}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANTS[kpi.status]} dot>
                      {KPI_STATUS_LABELS[kpi.status]}
                    </Badge>
                  </TableCell>
                  <TableCell>{kpi.unit || "—"}</TableCell>
                  <TableCell>{kpi.owner || "—"}</TableCell>
                  <TableCell>{kpi.refresh_frequency || "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
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
        <Hint>Only owners and editors can document KPIs.</Hint>
      )}
    </section>
  );
}

/** Marks a KPI the AI suggested. */
function AiLabel() {
  return (
    <Badge variant="accent" title="Suggested by the AI" className="ml-2">
      AI-generated
    </Badge>
  );
}

/** Why the AI suggested a KPI. */
function AiRationale({ kpi }: { kpi: Kpi }) {
  if (kpi.origin !== "ai") {
    return null;
  }
  return (
    <Hint aria-label="AI rationale">
      <AiLabel /> {kpi.rationale ?? "No rationale was given."}
    </Hint>
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
    <form className={formCardClasses} onSubmit={onSubmit} aria-label="Add KPI">
      <h4 className={h4Classes}>Document a KPI</h4>
      <KpiFields />
      {(problem || create.isError) && (
        <ErrorText>{problem ?? create.error?.message}</ErrorText>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          Add KPI
        </Button>
      </Actions>
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
      <section aria-label={`KPI ${kpi.name}`} className={formCardClasses}>
        <h4 className={h4Classes}>{kpi.name}</h4>
        <AiRationale kpi={kpi} />
        <DetailList>
          <dt>Status</dt>
          <dd>
            <Badge variant={STATUS_VARIANTS[kpi.status]} dot>
              {KPI_STATUS_LABELS[kpi.status]}
            </Badge>
          </dd>
          <dt>Business definition</dt>
          <dd>{kpi.definition || "—"}</dd>
          <dt>Formula in words</dt>
          <dd>{kpi.formula_text || "—"}</dd>
          <dt>Formula SQL</dt>
          <dd>{kpi.formula_sql ? <code className={monoClasses}>{kpi.formula_sql}</code> : "—"}</dd>
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
        </DetailList>
        <Actions>
          <Button type="button" onClick={onClose}>
            Close
          </Button>
        </Actions>
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
    update.mutate(
      { version: kpi.version, ...fields, status },
      { onSuccess: () => toast.success("Saved.") },
    );
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form
      key={`${kpi.id}-${kpi.version}`}
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-label={`KPI ${kpi.name}`}
    >
      <h4 className={h4Classes}>{kpi.name}</h4>
      <AiRationale kpi={kpi} />
      <label className={labelClasses}>
        Status
        <NativeSelect name="status" defaultValue={kpi.status}>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {KPI_STATUS_LABELS[status]}
            </option>
          ))}
        </NativeSelect>
      </label>
      <KpiFields initial={kpi} />
      {versionConflict ? (
        <div role="alert" className="flex flex-col items-start gap-2 text-sm text-red-11">
          <p>
            Someone else changed this KPI since you opened it. Reload it to see their changes,
            then edit again.
          </p>
          <Button
            type="button"
            size="sm"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </Button>
        </div>
      ) : (
        (problem || update.isError || remove.isError) && (
          <ErrorText>{problem ?? update.error?.message ?? remove.error?.message}</ErrorText>
        )
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save
        </Button>
        <Button
          type="button"
          disabled={remove.isPending}
          onClick={() => remove.mutate(undefined, { onSuccess: onClose })}
        >
          Delete KPI
        </Button>
        <Button type="button" onClick={onClose}>
          Close
        </Button>
      </Actions>
    </form>
  );
}
