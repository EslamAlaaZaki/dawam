"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  ADDITIVITY_LABELS,
  COLUMN_ROLE_LABELS,
  FACT_TYPE_LABELS,
  NEUTRAL_TYPES,
  useCreateDwColumn,
  useCreateDwTable,
  useDeleteDwColumn,
  useDeleteDwTable,
  useDwTable,
  useDwTables,
  useUpdateDwTable,
  type CreateDwColumnRequest,
  type CreateDwTableRequest,
  type DwColumn,
  type DwLayer,
  type DwTable,
  type DwTableSummary,
} from "../api/dwModel";
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
import { StarPanel } from "../diagrams/StarPanel";
import { Loading } from "../shell/Loading";
import { MappingsGrid } from "./MappingsGrid";
import {
  Actions,
  DetailList,
  EmptyState,
  ErrorText,
  Hint,
  NativeSelect,
  TableCard,
  checkClasses,
  checkLabelClasses,
  h3Classes,
  h5Classes,
  labelClasses,
  linkButtonClasses,
  monoClasses,
  Panel,
} from "./parts";

/** A form below the table detail: a hairline above, the controls stacked. */
const subFormClasses =
  "flex max-w-xl flex-col gap-3.5 border-t border-sand-4 pt-4";

const LAYER_LABELS: Record<DwLayer, string> = {
  staging: "Staging",
  core: "Core",
  mart: "Mart",
};

const KIND_LABELS: Record<string, string> = {
  fact: "Fact",
  dimension: "Dimension",
  bridge: "Bridge",
  staging: "Staging",
  generated: "Generated",
  other: "Other",
};

const ROLE_LABELS: Record<DwColumn["role"], string> = {
  ...COLUMN_ROLE_LABELS,
  scd_valid_from: "SCD valid from",
  scd_valid_to: "SCD valid to",
  scd_current_flag: "SCD current flag",
  row_hash: "Row hash",
};

const LENGTH_TYPES = ["char", "string", "binary"];

type Reference = Pick<DwTableSummary, "id" | "name" | "layer">;

function typeText(type: DwColumn["data_type"]): string {
  if (type.length != null) {
    return `${type.type}(${type.length})`;
  }
  if (type.precision != null) {
    return `${type.type}(${type.precision}${type.scale != null ? `,${type.scale}` : ""})`;
  }
  return type.type;
}

function tableDetail(
  table: Pick<DwTableSummary, "kind" | "grain" | "scd_type" | "is_conformed">,
) {
  if (table.kind === "fact") {
    return table.grain ?? "";
  }
  if (table.kind === "dimension") {
    return `SCD ${table.scd_type}${table.is_conformed ? " · conformed" : ""}`;
  }
  return "—";
}

function whole(value: FormDataEntryValue | null): number | undefined {
  const text = String(value ?? "").trim();
  return text === "" ? undefined : Number(text);
}

/**
 * The Model folder of a Layer (spec stories 89-93a): its tables, one open at a time, and
 * for owners and editors forms to add and change tables and their columns. Staging Tables
 * come from the Source Schema, so that Layer's model is read-only.
 */
export function ModelPanel({
  workspace,
  layer,
}: {
  workspace: Workspace;
  layer: DwLayer;
}) {
  const tables = useDwTables(workspace.id, layer);
  // A Mart fact may point at Core conformed dimensions, so Core's tables are needed too.
  const core = useDwTables(workspace.id, "core");
  const [openId, setOpenId] = useState<string | null>(null);
  const label = LAYER_LABELS[layer];
  const editable = layer !== "staging" && allows(workspace, "dw_schema.edit");

  if (tables.isPending) {
    return <Loading />;
  }
  if (tables.isError) {
    return (
      <ErrorText>Could not load the model: {tables.error.message}</ErrorText>
    );
  }
  const references: Reference[] = [
    ...tables.data,
    ...(layer === "mart"
      ? (core.data ?? []).filter((t) => t.is_conformed)
      : []),
  ].filter((t) => t.kind === "dimension" || t.kind === "generated");

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <h3 id="folder-title" className={h3Classes}>
        {label} model
      </h3>
      {tables.data.length === 0 ? (
        <EmptyState>
          No tables in the {label} Layer yet.
          {layer === "staging" &&
            " Staging Tables are created from the Source Schema."}
        </EmptyState>
      ) : (
        <TableCard
          footer={
            <span>
              {tables.data.length}{" "}
              {tables.data.length === 1 ? "table" : "tables"}
            </span>
          }
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col">Kind</TableHead>
                <TableHead scope="col">Grain / history</TableHead>
                <TableHead scope="col">Columns</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {tables.data.map((table) => (
                <TableRow key={table.id}>
                  <TableCell>
                    <button
                      type="button"
                      className={linkButtonClasses}
                      aria-current={table.id === openId ? "true" : undefined}
                      onClick={() => setOpenId(table.id)}
                    >
                      {table.name}
                    </button>
                    {table.naming_violation_count > 0 && (
                      <Badge variant="warning" role="status" className="ml-2">
                        Naming: {table.naming_violation_count} to fix
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge>{KIND_LABELS[table.kind] ?? table.kind}</Badge>
                  </TableCell>
                  <TableCell className="text-sand-11">
                    {tableDetail(table)}
                  </TableCell>
                  <TableCell>{table.column_count}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {layer !== "staging" && (
        <StarPanel workspaceId={workspace.id} layer={layer} />
      )}
      {openId && (
        <TableDetails
          // Another table starts with fresh forms.
          key={openId}
          workspace={workspace}
          tableId={openId}
          references={references}
          editable={editable}
          onClose={() => setOpenId(null)}
        />
      )}
      {editable ? (
        <AddTableForm
          workspaceId={workspace.id}
          layer={layer}
          onAdded={(t) => setOpenId(t.id)}
        />
      ) : (
        layer !== "staging" && (
          <Hint>Only owners and editors can edit the model.</Hint>
        )
      )}
    </section>
  );
}

function AddTableForm({
  workspaceId,
  layer,
  onAdded,
}: {
  workspaceId: string;
  layer: DwLayer;
  onAdded: (table: DwTable) => void;
}) {
  const create = useCreateDwTable(workspaceId);
  const [kind, setKind] = useState<CreateDwTableRequest["kind"]>("fact");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const get = (name: string) => String(data.get(name) ?? "");
    const body: CreateDwTableRequest = { layer, name: get("name"), kind };
    if (kind === "fact") {
      body.grain = get("grain");
      body.fact_type = get("fact_type") as CreateDwTableRequest["fact_type"];
    }
    if (kind === "dimension") {
      body.scd_type = Number(get("scd_type"));
      if (data.get("is_conformed")) {
        body.is_conformed = true;
      }
    }
    create.mutate(body, {
      onSuccess: (table) => {
        form.reset();
        setKind("fact");
        onAdded(table);
      },
    });
  }

  return (
    <form className={subFormClasses} onSubmit={onSubmit} aria-label="Add table">
      <h4 className={h5Classes}>Add a table</h4>
      <label className={labelClasses}>
        Name
        <Input name="name" required maxLength={128} />
      </label>
      <label className={labelClasses}>
        Kind
        <NativeSelect
          name="kind"
          value={kind}
          onChange={(e) =>
            setKind(e.target.value as CreateDwTableRequest["kind"])
          }
        >
          <option value="fact">Fact</option>
          <option value="dimension">Dimension</option>
          <option value="bridge">Bridge</option>
        </NativeSelect>
      </label>
      {kind === "fact" && (
        <>
          <label className={labelClasses}>
            Grain
            <Input
              name="grain"
              required
              maxLength={1000}
              placeholder="One row per…"
            />
          </label>
          <label className={labelClasses}>
            Fact type
            <NativeSelect name="fact_type" required defaultValue="">
              <option value="" disabled>
                Choose…
              </option>
              {Object.entries(FACT_TYPE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </NativeSelect>
          </label>
        </>
      )}
      {kind === "dimension" && (
        <>
          <label className={labelClasses}>
            SCD type
            <NativeSelect name="scd_type" defaultValue="1">
              <option value="0">0 (fixed)</option>
              <option value="1">1 (overwrite)</option>
              <option value="2">2 (keep history)</option>
            </NativeSelect>
          </label>
          <label className={checkLabelClasses}>
            <input
              type="checkbox"
              className={checkClasses}
              name="is_conformed"
            />{" "}
            Conformed dimension
          </label>
        </>
      )}
      {create.isError && <ErrorText>{create.error.message}</ErrorText>}
      <Actions>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          Add table
        </Button>
      </Actions>
    </form>
  );
}

function TableDetails({
  workspace,
  tableId,
  references,
  editable,
  onClose,
}: {
  workspace: Workspace;
  tableId: string;
  references: Reference[];
  editable: boolean;
  onClose: () => void;
}) {
  const table = useDwTable(workspace.id, tableId);
  const removeColumn = useDeleteDwColumn(workspace.id, tableId);
  const removeTable = useDeleteDwTable(workspace.id, tableId);
  // Deleting a table is a soft delete, for owners only; it is purged from the deleted list.
  const canDelete = allows(workspace, "objects.delete");
  const [showMappings, setShowMappings] = useState(false);

  if (table.isPending) {
    return <Loading />;
  }
  if (table.isError) {
    return (
      <ErrorText>Could not load the table: {table.error.message}</ErrorText>
    );
  }
  const t = table.data;
  const names = new Map(references.map((r) => [r.id, r.name]));

  return (
    <Panel
      aria-label={`Table ${t.name}`}
      className="min-w-0 gap-0 overflow-hidden p-0"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-sand-5 px-4 py-3">
        <h4 className="font-mono text-md/[1.3] font-semibold">{t.name}</h4>
        <Badge variant="accent">{KIND_LABELS[t.kind] ?? t.kind}</Badge>
        <span className="text-sm text-sand-11">
          {LAYER_LABELS[t.layer]} Layer
        </span>
        <div className="ml-auto flex gap-1.5">
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
      <div className="flex gap-4 border-b border-line px-4">
        <span className="-mb-px border-b-2 border-sand-12 py-2.5 font-medium text-sand-12">
          Columns
        </span>
        {t.layer !== "staging" && (
          <button
            type="button"
            aria-expanded={showMappings}
            className={`-mb-px cursor-pointer border-b-2 py-2.5 font-medium hover:text-sand-12 ${
              showMappings
                ? "border-sand-12 text-sand-12"
                : "border-transparent text-sand-11"
            }`}
            onClick={() => setShowMappings((open) => !open)}
          >
            {showMappings ? "Hide mappings" : "Mappings"}
          </button>
        )}
      </div>
      <div className="flex flex-col gap-4 p-4">
        {t.naming_violations.length > 0 && (
          <ul aria-label="Naming violations" className="text-sm text-red-11">
            {t.naming_violations.map((v) => (
              <li key={v.code}>{v.message}</li>
            ))}
          </ul>
        )}
        <DetailList>
          <dt>Kind</dt>
          <dd>{KIND_LABELS[t.kind] ?? t.kind}</dd>
          {t.kind === "fact" && (
            <>
              <dt>Grain</dt>
              <dd>{t.grain}</dd>
              <dt>Fact type</dt>
              <dd>{t.fact_type ? FACT_TYPE_LABELS[t.fact_type] : "—"}</dd>
            </>
          )}
          {t.kind === "dimension" && (
            <>
              <dt>History</dt>
              <dd>{tableDetail(t)}</dd>
            </>
          )}
        </DetailList>
        {t.unknown_member && (
          <Hint>
            Unknown member: surrogate key {t.unknown_member.surrogate_key}
            {Object.keys(t.unknown_member.defaults).length > 0 &&
              `, defaults ${Object.entries(t.unknown_member.defaults)
                .map(([column, value]) => `${column} = ${String(value)}`)
                .join(", ")}`}
          </Hint>
        )}
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Column</TableHead>
                <TableHead scope="col">Type</TableHead>
                <TableHead scope="col">Role</TableHead>
                <TableHead scope="col">Details</TableHead>
                {editable && <TableHead scope="col">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {t.columns.map((column) => (
                <TableRow key={column.id}>
                  <TableCell className={monoClasses}>
                    {column.name}
                    {column.naming_violations.map((v) => (
                      <Badge
                        key={v.code}
                        variant="warning"
                        role="status"
                        className="ml-2 font-sans"
                      >
                        {v.message}
                      </Badge>
                    ))}
                  </TableCell>
                  <TableCell className={`${monoClasses} text-sand-11`}>
                    {typeText(column.data_type)}
                    {column.is_nullable ? "" : " not null"}
                  </TableCell>
                  <TableCell>{ROLE_LABELS[column.role]}</TableCell>
                  <TableCell className="text-sand-11">
                    {[
                      column.additivity && ADDITIVITY_LABELS[column.additivity],
                      column.references_table_id &&
                        `→ ${names.get(column.references_table_id) ?? "another table"}${
                          column.role_name ? ` (${column.role_name})` : ""
                        }`,
                      column.scd_type_override != null &&
                        `SCD ${column.scd_type_override}`,
                      column.is_system && "DAWAM",
                    ]
                      .filter(Boolean)
                      .join(" · ") || "—"}
                  </TableCell>
                  {editable && (
                    <TableCell>
                      {!column.is_system && (
                        <Button
                          type="button"
                          size="sm"
                          aria-label={`Delete ${column.name}`}
                          disabled={removeColumn.isPending}
                          onClick={() => removeColumn.mutate(column.id)}
                        >
                          Delete
                        </Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
        {t.layer !== "staging" && showMappings && (
          <MappingsGrid
            workspaceId={workspace.id}
            tableId={t.id}
            editable={editable}
          />
        )}
        {(removeColumn.isError || removeTable.isError) && (
          <ErrorText>
            {removeColumn.error?.message ?? removeTable.error?.message}
          </ErrorText>
        )}
        {editable && (
          <>
            <EditTableForm
              workspaceId={workspace.id}
              table={t}
              reload={() => table.refetch()}
            />
            <AddColumnForm
              workspaceId={workspace.id}
              table={t}
              references={references.filter((r) => r.id !== t.id)}
            />
            {canDelete && (
              <Actions>
                <Button
                  type="button"
                  disabled={removeTable.isPending}
                  onClick={() =>
                    removeTable.mutate(undefined, { onSuccess: onClose })
                  }
                >
                  Delete table
                </Button>
              </Actions>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}

function EditTableForm({
  workspaceId,
  table,
  reload,
}: {
  workspaceId: string;
  table: DwTable;
  reload: () => void;
}) {
  const update = useUpdateDwTable(workspaceId, table.id);
  const conflict =
    update.error instanceof ApiError &&
    update.error.code === "version_conflict";

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const get = (name: string) => String(data.get(name) ?? "");
    update.mutate(
      {
        version: table.version,
        name: get("name"),
        description: get("description"),
        ...(table.kind === "fact"
          ? {
              grain: get("grain"),
              fact_type: get("fact_type") as NonNullable<DwTable["fact_type"]>,
            }
          : {}),
        ...(table.kind === "dimension"
          ? {
              scd_type: Number(get("scd_type")),
              is_conformed: data.get("is_conformed") !== null,
            }
          : {}),
      },
      { onSuccess: () => toast.success("Saved.") },
    );
  }

  return (
    <form
      // A new version (saved, or reloaded) refills the inputs.
      key={`${table.id}-${table.version}`}
      className={subFormClasses}
      onSubmit={onSubmit}
      aria-label={`Edit ${table.name}`}
    >
      <h5 className={h5Classes}>Edit table</h5>
      <label className={labelClasses}>
        Name
        <Input name="name" required maxLength={128} defaultValue={table.name} />
      </label>
      <label className={labelClasses}>
        Description
        <Textarea
          name="description"
          rows={2}
          maxLength={4000}
          defaultValue={table.description}
        />
      </label>
      {table.kind === "fact" && (
        <>
          <label className={labelClasses}>
            Grain
            <Input
              name="grain"
              required
              maxLength={1000}
              defaultValue={table.grain ?? ""}
            />
          </label>
          <label className={labelClasses}>
            Fact type
            <NativeSelect
              name="fact_type"
              defaultValue={table.fact_type ?? "transactional"}
            >
              {Object.entries(FACT_TYPE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </NativeSelect>
          </label>
        </>
      )}
      {table.kind === "dimension" && (
        <>
          <label className={labelClasses}>
            SCD type
            <NativeSelect
              name="scd_type"
              defaultValue={String(table.scd_type ?? 1)}
            >
              <option value="0">0 (fixed)</option>
              <option value="1">1 (overwrite)</option>
              <option value="2">2 (keep history)</option>
            </NativeSelect>
          </label>
          <label className={checkLabelClasses}>
            <Input
              type="checkbox"
              className={checkClasses}
              name="is_conformed"
              defaultChecked={table.is_conformed}
            />{" "}
            Conformed dimension
          </label>
        </>
      )}
      {conflict ? (
        <div
          role="alert"
          className="flex flex-col items-start gap-2 text-sm text-red-11"
        >
          <p>
            Someone else changed this table since you opened it. Reload it to
            see their changes, then edit again.
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
        update.isError && <ErrorText>{update.error.message}</ErrorText>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save
        </Button>
      </Actions>
    </form>
  );
}

function AddColumnForm({
  workspaceId,
  table,
  references,
}: {
  workspaceId: string;
  table: DwTable;
  references: Reference[];
}) {
  const create = useCreateDwColumn(workspaceId, table.id);
  const [dataType, setDataType] = useState<string>("string");
  const [role, setRole] =
    useState<keyof typeof COLUMN_ROLE_LABELS>("attribute");
  const roles = (
    Object.keys(COLUMN_ROLE_LABELS) as (keyof typeof COLUMN_ROLE_LABELS)[]
  ).filter(
    (r) =>
      table.kind === "fact" ||
      (r !== "measure" && r !== "degenerate_dimension"),
  );

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const get = (name: string) => String(data.get(name) ?? "").trim();
    const type: CreateDwColumnRequest["data_type"] = { type: dataType };
    if (LENGTH_TYPES.includes(dataType)) {
      type.length = whole(data.get("length"));
    }
    if (dataType === "decimal") {
      type.precision = whole(data.get("precision"));
      type.scale = whole(data.get("scale"));
    }
    const body: CreateDwColumnRequest = {
      name: get("name"),
      data_type: type,
      role,
      is_nullable: data.get("is_nullable") !== null,
    };
    if (role === "measure" && get("additivity")) {
      body.additivity = get(
        "additivity",
      ) as CreateDwColumnRequest["additivity"];
    }
    if (role === "fk") {
      body.references_table_id = get("references_table_id") || null;
      if (get("role_name")) {
        body.role_name = get("role_name");
      }
    }
    if (get("semantic_type")) {
      body.semantic_type = get("semantic_type");
    }
    if (get("description")) {
      body.description = get("description");
    }
    create.mutate(body, { onSuccess: () => form.reset() });
  }

  return (
    <form
      className={subFormClasses}
      onSubmit={onSubmit}
      aria-label="Add column"
    >
      <h5 className={h5Classes}>Add a column</h5>
      <label className={labelClasses}>
        Name
        <Input name="name" required maxLength={128} />
      </label>
      <label className={labelClasses}>
        Data type
        <NativeSelect
          name="data_type"
          value={dataType}
          onChange={(e) => setDataType(e.target.value)}
        >
          {NEUTRAL_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </NativeSelect>
      </label>
      {LENGTH_TYPES.includes(dataType) && (
        <label className={labelClasses}>
          Length
          <Input name="length" type="number" min={1} />
        </label>
      )}
      {dataType === "decimal" && (
        <>
          <label className={labelClasses}>
            Precision
            <Input name="precision" type="number" min={1} max={38} />
          </label>
          <label className={labelClasses}>
            Scale
            <Input name="scale" type="number" min={0} max={38} />
          </label>
        </>
      )}
      <label className={labelClasses}>
        Role
        <NativeSelect
          name="role"
          value={role}
          onChange={(e) =>
            setRole(e.target.value as keyof typeof COLUMN_ROLE_LABELS)
          }
        >
          {roles.map((value) => (
            <option key={value} value={value}>
              {COLUMN_ROLE_LABELS[value]}
            </option>
          ))}
        </NativeSelect>
      </label>
      {role === "measure" && (
        <label className={labelClasses}>
          Additivity
          <NativeSelect name="additivity" defaultValue="">
            <option value="">Not declared</option>
            {Object.entries(ADDITIVITY_LABELS).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </NativeSelect>
        </label>
      )}
      {role === "fk" && (
        <>
          <label className={labelClasses}>
            References
            <NativeSelect name="references_table_id" required defaultValue="">
              <option value="" disabled>
                Choose a dimension…
              </option>
              {references.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({LAYER_LABELS[r.layer]})
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className={labelClasses}>
            Role name
            <Input name="role_name" maxLength={128} placeholder="order_date" />
          </label>
        </>
      )}
      <label className={labelClasses}>
        Semantic type
        <Input
          name="semantic_type"
          maxLength={64}
          placeholder="amount, quantity…"
        />
      </label>
      <label className={labelClasses}>
        Description
        <Input name="description" maxLength={4000} />
      </label>
      <label className={checkLabelClasses}>
        <input
          type="checkbox"
          className={checkClasses}
          name="is_nullable"
          defaultChecked
        />{" "}
        Nullable
      </label>
      {create.isError && <ErrorText>{create.error.message}</ErrorText>}
      <Actions>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          Add column
        </Button>
      </Actions>
    </form>
  );
}
