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
import { StarPanel } from "../diagrams/StarPanel";
import { MappingsGrid } from "./MappingsGrid";

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
    return <p>Loading…</p>;
  }
  if (tables.isError) {
    return <p role="alert">Could not load the model: {tables.error.message}</p>;
  }
  const references: Reference[] = [
    ...tables.data,
    ...(layer === "mart"
      ? (core.data ?? []).filter((t) => t.is_conformed)
      : []),
  ].filter((t) => t.kind === "dimension" || t.kind === "generated");

  return (
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">{label} model</h3>
      {tables.data.length === 0 ? (
        <p className="empty-state">
          No tables in the {label} Layer yet.
          {layer === "staging" &&
            " Staging Tables are created from the Source Schema."}
        </p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Kind</th>
              <th scope="col">Grain / history</th>
              <th scope="col">Columns</th>
            </tr>
          </thead>
          <tbody>
            {tables.data.map((table) => (
              <tr key={table.id}>
                <td>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setOpenId(table.id)}
                  >
                    {table.name}
                  </button>
                  {table.naming_violation_count > 0 && (
                    <small role="status">
                      {" "}
                      Naming: {table.naming_violation_count} to fix
                    </small>
                  )}
                </td>
                <td>{KIND_LABELS[table.kind] ?? table.kind}</td>
                <td>{tableDetail(table)}</td>
                <td>{table.column_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {layer !== "staging" && <StarPanel workspaceId={workspace.id} layer={layer} />}
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
          <p>Only owners and editors can edit the model.</p>
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
    <form className="form" onSubmit={onSubmit} aria-label="Add table">
      <h4>Add a table</h4>
      <label>
        Name
        <input name="name" required maxLength={128} />
      </label>
      <label>
        Kind
        <select
          name="kind"
          value={kind}
          onChange={(e) =>
            setKind(e.target.value as CreateDwTableRequest["kind"])
          }
        >
          <option value="fact">Fact</option>
          <option value="dimension">Dimension</option>
          <option value="bridge">Bridge</option>
        </select>
      </label>
      {kind === "fact" && (
        <>
          <label>
            Grain
            <input
              name="grain"
              required
              maxLength={1000}
              placeholder="One row per…"
            />
          </label>
          <label>
            Fact type
            <select name="fact_type" required defaultValue="">
              <option value="" disabled>
                Choose…
              </option>
              {Object.entries(FACT_TYPE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {kind === "dimension" && (
        <>
          <label>
            SCD type
            <select name="scd_type" defaultValue="1">
              <option value="0">0 (fixed)</option>
              <option value="1">1 (overwrite)</option>
              <option value="2">2 (keep history)</option>
            </select>
          </label>
          <label>
            <input type="checkbox" name="is_conformed" /> Conformed dimension
          </label>
        </>
      )}
      {create.isError && (
        <p className="form-error" role="alert">
          {create.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={create.isPending}>
          Add table
        </button>
      </div>
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
  const [showMappings, setShowMappings] = useState(false);

  if (table.isPending) {
    return <p>Loading…</p>;
  }
  if (table.isError) {
    return <p role="alert">Could not load the table: {table.error.message}</p>;
  }
  const t = table.data;
  const names = new Map(references.map((r) => [r.id, r.name]));

  return (
    <section aria-label={`Table ${t.name}`}>
      <h4>{t.name}</h4>
      {t.naming_violations.length > 0 && (
        <ul aria-label="Naming violations">
          {t.naming_violations.map((v) => (
            <li key={v.code}>{v.message}</li>
          ))}
        </ul>
      )}
      <dl className="details">
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
      </dl>
      {t.unknown_member && (
        <p>
          Unknown member: surrogate key {t.unknown_member.surrogate_key}
          {Object.keys(t.unknown_member.defaults).length > 0 &&
            `, defaults ${Object.entries(t.unknown_member.defaults)
              .map(([column, value]) => `${column} = ${String(value)}`)
              .join(", ")}`}
        </p>
      )}
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Type</th>
            <th scope="col">Role</th>
            <th scope="col">Details</th>
            {editable && <th scope="col">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {t.columns.map((column) => (
            <tr key={column.id}>
              <td>
                {column.name}
                {column.naming_violations.map((v) => (
                  <small key={v.code} role="status">
                    {" "}
                    {v.message}
                  </small>
                ))}
              </td>
              <td>
                {typeText(column.data_type)}
                {column.is_nullable ? "" : " not null"}
              </td>
              <td>{ROLE_LABELS[column.role]}</td>
              <td>
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
              </td>
              {editable && (
                <td>
                  {!column.is_system && (
                    <button
                      type="button"
                      className="secondary"
                      aria-label={`Delete ${column.name}`}
                      disabled={removeColumn.isPending}
                      onClick={() => removeColumn.mutate(column.id)}
                    >
                      Delete
                    </button>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {t.layer !== "staging" && (
        <>
          <button
            type="button"
            className="secondary"
            aria-expanded={showMappings}
            onClick={() => setShowMappings((open) => !open)}
          >
            {showMappings ? "Hide mappings" : "Mappings"}
          </button>
          {showMappings && (
            <MappingsGrid workspaceId={workspace.id} tableId={t.id} editable={editable} />
          )}
        </>
      )}
      {(removeColumn.isError || removeTable.isError) && (
        <p className="form-error" role="alert">
          {removeColumn.error?.message ?? removeTable.error?.message}
        </p>
      )}
      {editable ? (
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
          <div className="form-actions">
            <button
              type="button"
              className="secondary"
              disabled={removeTable.isPending}
              onClick={() =>
                removeTable.mutate(undefined, { onSuccess: onClose })
              }
            >
              Delete table
            </button>
            <button type="button" className="secondary" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : (
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
      )}
    </section>
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
    update.mutate({
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
    });
  }

  return (
    <form
      // A new version (saved, or reloaded) refills the inputs.
      key={`${table.id}-${table.version}`}
      className="form"
      onSubmit={onSubmit}
      aria-label={`Edit ${table.name}`}
    >
      <h5>Edit table</h5>
      <label>
        Name
        <input name="name" required maxLength={128} defaultValue={table.name} />
      </label>
      <label>
        Description
        <textarea
          name="description"
          rows={2}
          maxLength={4000}
          defaultValue={table.description}
        />
      </label>
      {table.kind === "fact" && (
        <>
          <label>
            Grain
            <input
              name="grain"
              required
              maxLength={1000}
              defaultValue={table.grain ?? ""}
            />
          </label>
          <label>
            Fact type
            <select
              name="fact_type"
              defaultValue={table.fact_type ?? "transactional"}
            >
              {Object.entries(FACT_TYPE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {table.kind === "dimension" && (
        <>
          <label>
            SCD type
            <select name="scd_type" defaultValue={String(table.scd_type ?? 1)}>
              <option value="0">0 (fixed)</option>
              <option value="1">1 (overwrite)</option>
              <option value="2">2 (keep history)</option>
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              name="is_conformed"
              defaultChecked={table.is_conformed}
            />{" "}
            Conformed dimension
          </label>
        </>
      )}
      {conflict ? (
        <div className="form-error" role="alert">
          <p>
            Someone else changed this table since you opened it. Reload it to
            see their changes, then edit again.
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
        update.isError && (
          <p className="form-error" role="alert">
            {update.error.message}
          </p>
        )
      )}
      {update.isSuccess && <p role="status">Saved.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
      </div>
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
    <form className="form" onSubmit={onSubmit} aria-label="Add column">
      <h5>Add a column</h5>
      <label>
        Name
        <input name="name" required maxLength={128} />
      </label>
      <label>
        Data type
        <select
          name="data_type"
          value={dataType}
          onChange={(e) => setDataType(e.target.value)}
        >
          {NEUTRAL_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>
      {LENGTH_TYPES.includes(dataType) && (
        <label>
          Length
          <input name="length" type="number" min={1} />
        </label>
      )}
      {dataType === "decimal" && (
        <>
          <label>
            Precision
            <input name="precision" type="number" min={1} max={38} />
          </label>
          <label>
            Scale
            <input name="scale" type="number" min={0} max={38} />
          </label>
        </>
      )}
      <label>
        Role
        <select
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
        </select>
      </label>
      {role === "measure" && (
        <label>
          Additivity
          <select name="additivity" defaultValue="">
            <option value="">Not declared</option>
            {Object.entries(ADDITIVITY_LABELS).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
        </label>
      )}
      {role === "fk" && (
        <>
          <label>
            References
            <select name="references_table_id" required defaultValue="">
              <option value="" disabled>
                Choose a dimension…
              </option>
              {references.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({LAYER_LABELS[r.layer]})
                </option>
              ))}
            </select>
          </label>
          <label>
            Role name
            <input name="role_name" maxLength={128} placeholder="order_date" />
          </label>
        </>
      )}
      <label>
        Semantic type
        <input
          name="semantic_type"
          maxLength={64}
          placeholder="amount, quantity…"
        />
      </label>
      <label>
        Description
        <input name="description" maxLength={4000} />
      </label>
      <label>
        <input type="checkbox" name="is_nullable" defaultChecked /> Nullable
      </label>
      {create.isError && (
        <p className="form-error" role="alert">
          {create.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={create.isPending}>
          Add column
        </button>
      </div>
    </form>
  );
}
