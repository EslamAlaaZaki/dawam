"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

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
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { useReturnFocus } from "../components/entity/useReturnFocus";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Textarea } from "../components/ui/textarea";
import {
  DetailList,
  Hint,
  NativeSelect,
  h3Classes,
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
function readFields(data: FormData): Fields | string {
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

const CONFLICT_MESSAGE =
  "Someone else changed this KPI since you opened it. The latest version is loaded: check your edits, then save again.";

/**
 * The KPIs folder of a Source System (`systemId`) or of the Data Warehouse (`systemId`
 * null): the KPIs documented there, and for owners and editors a popup to document
 * another and per-row Edit and Delete.
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
  const [adding, setAdding] = useState(false);
  const canEdit = allows(workspace, "kpi.edit");
  const addButton = canEdit ? (
    <Button variant="primary" onClick={() => setAdding(true)}>
      <Plus aria-hidden />
      Add KPI
    </Button>
  ) : null;
  const columns: EntityColumn<Kpi>[] = [
    {
      header: "Name",
      cell: (kpi) => (
        <>
          <KpiViewButton kpi={kpi} />
          {kpi.origin === "ai" && <AiLabel />}
        </>
      ),
    },
    {
      header: "Status",
      cell: (kpi) => (
        <Badge variant={STATUS_VARIANTS[kpi.status]} dot>
          {KPI_STATUS_LABELS[kpi.status]}
        </Badge>
      ),
    },
    { header: "Unit", cell: (kpi) => kpi.unit || "—" },
    { header: "Owner", cell: (kpi) => kpi.owner || "—" },
    { header: "Refresh", cell: (kpi) => kpi.refresh_frequency || "—" },
  ];

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="folder-title" className={h3Classes}>
          {title}
        </h3>
        {addButton && <span className="ml-auto">{addButton}</span>}
      </div>
      <EntityTable
        rows={kpis.data}
        rowKey={(kpi) => kpi.id}
        rowLabel={(kpi) => kpi.name}
        columns={columns}
        actions={
          canEdit
            ? (kpi) => (
                <KpiActions
                  workspaceId={workspace.id}
                  kpi={kpi}
                  reload={() => void kpis.refetch()}
                />
              )
            : undefined
        }
        isPending={kpis.isPending}
        error={kpis.error}
        loadErrorPrefix="Could not load the KPIs:"
        emptyMessage={
          scope.systemId
            ? "No KPIs yet. KPIs based on this system will be listed here."
            : "No KPIs yet. KPIs that span systems will be listed here."
        }
      />
      {!canEdit && <Hint>Only owners and editors can document KPIs.</Hint>}
      {canEdit && (
        <AddKpiDialog
          workspaceId={workspace.id}
          scope={scope}
          open={adding}
          onOpenChange={setAdding}
        />
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

/** The KPI's name, which opens its details read-only (for every role). */
function KpiViewButton({ kpi }: { kpi: Kpi }) {
  const [open, setOpen] = useState(false);
  const returnFocus = useReturnFocus(open);

  return (
    <>
      <button type="button" className={linkButtonClasses} onClick={() => setOpen(true)}>
        {kpi.name}
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent onCloseAutoFocus={returnFocus}>
          <DialogHeader>
            <DialogTitle>{kpi.name}</DialogTitle>
            <DialogDescription>KPI details</DialogDescription>
          </DialogHeader>
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
            <dd>
              {kpi.formula_sql ? (
                <code className={monoClasses}>{kpi.formula_sql}</code>
              ) : (
                "—"
              )}
            </dd>
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
          <DialogFooter>
            <DialogClose asChild>
              <Button type="button">Close</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function AddKpiDialog({
  workspaceId,
  scope,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  scope: KpiScope;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateKpi(workspaceId);
  const [problem, setProblem] = useState<string | null>(null);

  function onSubmit(form: FormData) {
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
          toast.success(`${kpi.name} added.`);
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          create.reset();
          setProblem(null);
        }
        onOpenChange(next);
      }}
      title="Add KPI"
      description="Document a KPI: what it means and how it is calculated."
      submitLabel="Add KPI"
      onSubmit={onSubmit}
      pending={create.isPending}
      error={problem ? new Error(problem) : create.error}
    >
      <KpiFields />
    </EntityFormDialog>
  );
}

/** What owners and editors can do to one KPI; deleting asks first. */
function KpiActions({
  workspaceId,
  kpi,
  reload,
}: {
  workspaceId: string;
  kpi: Kpi;
  reload: () => void;
}) {
  const remove = useDeleteKpi(workspaceId, kpi.id);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button size="sm" className="text-red-11" onClick={() => setDeleting(true)}>
          Delete
        </Button>
      </div>
      <EditKpiDialog
        workspaceId={workspaceId}
        kpi={kpi}
        reload={reload}
        open={editing}
        onOpenChange={setEditing}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setDeleting(next);
        }}
        title={`Delete ${kpi.name}?`}
        description="The KPI is removed from this list."
        confirmLabel="Delete KPI"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() =>
          remove.mutate(undefined, { onSuccess: () => setDeleting(false) })
        }
      />
    </>
  );
}

/** One KPI's fields and status, saved against the version that was opened. */
function EditKpiDialog({
  workspaceId,
  kpi,
  reload,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  kpi: Kpi;
  reload: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateKpi(workspaceId, kpi.id);
  const [problem, setProblem] = useState<string | null>(null);
  const conflict =
    update.error instanceof ApiError && update.error.code === "version_conflict";

  function onSubmit(form: FormData) {
    const fields = readFields(form);
    if (typeof fields === "string") {
      setProblem(fields);
      return;
    }
    setProblem(null);
    const status = String(form.get("status")) as KpiStatus;
    update.mutate(
      { version: kpi.version, ...fields, status },
      {
        onSuccess: () => {
          toast.success("Saved.");
          onOpenChange(false);
        },
        // The newer version loads, so saving again works.
        onError: (error) => {
          if (error instanceof ApiError && error.code === "version_conflict") {
            reload();
          }
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          update.reset();
          setProblem(null);
        }
        onOpenChange(next);
      }}
      title="Edit KPI"
      description={kpi.name}
      submitLabel="Save"
      onSubmit={onSubmit}
      pending={update.isPending}
      error={
        problem
          ? new Error(problem)
          : conflict
            ? new Error(CONFLICT_MESSAGE)
            : update.error
      }
    >
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
    </EntityFormDialog>
  );
}
