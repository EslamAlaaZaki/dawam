"use client";

import { useState } from "react";

import {
  MAPPING_TYPE_LABELS,
  TABLE_LEVEL_MAPPING_TYPES,
  useSaveColumnMapping,
  useTableMapping,
  type ColumnMapping,
  type MappingType,
} from "../api/mappings";

/**
 * The mappings editor of one Core or Mart table (spec stories 99, 100, 104): a grid with a
 * row per column, mapped from the Layer below. The SQL is the master; the inputs shown are
 * derived from it on every save, never edited.
 */
export function MappingsGrid({
  workspaceId,
  tableId,
  editable,
}: {
  workspaceId: string;
  tableId: string;
  editable: boolean;
}) {
  const mapping = useTableMapping(workspaceId, tableId);

  if (mapping.isPending) {
    return <p>Loading mappings…</p>;
  }
  if (mapping.isError) {
    return <p role="alert">Could not load the mappings: {mapping.error.message}</p>;
  }
  const m = mapping.data;

  return (
    <section aria-label={`Mappings of ${m.table_name}`}>
      <h5>Mappings from the {m.source_layer} Layer</h5>
      <p>
        Write each input as <code>table.column</code> of a {m.source_layer} table.
      </p>
      <table className="admin-table">
        <thead>
          <tr>
            <th scope="col">Column</th>
            <th scope="col">Type</th>
            <th scope="col">Rule</th>
            <th scope="col">SQL expression</th>
            <th scope="col">Inputs</th>
            {editable && <th scope="col">Actions</th>}
          </tr>
        </thead>
        <tbody>
          {m.columns.map((column) => (
            <MappingRow
              key={`${column.column_id}:${column.version}`}
              workspaceId={workspaceId}
              tableId={tableId}
              column={column}
              editable={editable}
            />
          ))}
        </tbody>
      </table>
    </section>
  );
}

function MappingRow({
  workspaceId,
  tableId,
  column,
  editable,
}: {
  workspaceId: string;
  tableId: string;
  column: ColumnMapping;
  editable: boolean;
}) {
  const save = useSaveColumnMapping(workspaceId, tableId, column.column_id);
  const [type, setType] = useState<MappingType>(column.mapping_type);
  const [rule, setRule] = useState(column.rule_text);
  const [sql, setSql] = useState(column.sql_expression);
  const name = column.column_name;
  const errors = column.validation.errors;

  const inputs = [...column.inputs, ...column.uses].map((i) => `${i.table_name}.${i.column_name}`);

  return (
    <tr>
      <td>{name}</td>
      {editable ? (
        <>
          <td>
            <select
              aria-label={`Type of ${name}`}
              value={type}
              onChange={(e) => setType(e.target.value as MappingType)}
            >
              {TABLE_LEVEL_MAPPING_TYPES.map((t) => (
                <option key={t} value={t}>
                  {MAPPING_TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </td>
          <td>
            <input
              aria-label={`Rule for ${name}`}
              value={rule}
              maxLength={4000}
              onChange={(e) => setRule(e.target.value)}
            />
          </td>
          <td>
            <input
              aria-label={`SQL for ${name}`}
              value={sql}
              disabled={type === "unmapped"}
              onChange={(e) => setSql(e.target.value)}
            />
          </td>
        </>
      ) : (
        <>
          <td>{MAPPING_TYPE_LABELS[column.mapping_type]}</td>
          <td>{column.rule_text || "—"}</td>
          <td>{column.sql_expression ? <code>{column.sql_expression}</code> : "—"}</td>
        </>
      )}
      <td>
        {inputs.length > 0 ? inputs.join(", ") : "—"}
        {errors.map((e) => (
          <p key={e.code} className="form-error">
            {e.message}
          </p>
        ))}
        {save.isError && (
          <p className="form-error" role="alert">
            {save.error.message}
          </p>
        )}
      </td>
      {editable && (
        <td>
          <button
            type="button"
            aria-label={`Save ${name}`}
            disabled={save.isPending}
            onClick={() =>
              save.mutate({
                mapping_type: type,
                rule_text: rule,
                sql_expression: type === "unmapped" ? "" : sql,
                version: column.version,
              })
            }
          >
            Save
          </button>
        </td>
      )}
    </tr>
  );
}
