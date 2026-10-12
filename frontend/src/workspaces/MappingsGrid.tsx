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
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
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
  ErrorText,
  Hint,
  NativeSelect,
  TableCard,
  h5Classes,
  monoClasses,
} from "./parts";

/** A column's mapping state as a pill: unmapped is the one that still needs work. */
function MappingState({ type }: { type: MappingType }) {
  return (
    <Badge variant={type === "unmapped" ? "neutral" : "success"} dot>
      {MAPPING_TYPE_LABELS[type]}
    </Badge>
  );
}

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
    return <Loading label="Loading mappings…" />;
  }
  if (mapping.isError) {
    return (
      <ErrorText>
        Could not load the mappings: {mapping.error.message}
      </ErrorText>
    );
  }
  const m = mapping.data;

  return (
    <section
      aria-label={`Mappings of ${m.table_name}`}
      className="flex flex-col gap-3"
    >
      <h5 className={h5Classes}>Mappings from the {m.source_layer} Layer</h5>
      <Hint>
        Write each input as <code className={monoClasses}>table.column</code> of
        a {m.source_layer} table.
      </Hint>
      <TableCard>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">Column</TableHead>
              <TableHead scope="col">Type</TableHead>
              <TableHead scope="col">Rule</TableHead>
              <TableHead scope="col">SQL expression</TableHead>
              <TableHead scope="col">Inputs</TableHead>
              {editable && <TableHead scope="col">Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {m.columns.map((column) => (
              <MappingRow
                key={`${column.column_id}:${column.version}`}
                workspaceId={workspaceId}
                tableId={tableId}
                column={column}
                editable={editable}
              />
            ))}
          </TableBody>
        </Table>
      </TableCard>
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

  const inputs = [...column.inputs, ...column.uses].map(
    (i) => `${i.table_name}.${i.column_name}`,
  );

  return (
    <TableRow>
      <TableCell className={monoClasses}>{name}</TableCell>
      {editable ? (
        <>
          <TableCell>
            <NativeSelect
              aria-label={`Type of ${name}`}
              className="h-8 w-auto"
              value={type}
              onChange={(e) => setType(e.target.value as MappingType)}
            >
              {TABLE_LEVEL_MAPPING_TYPES.map((t) => (
                <option key={t} value={t}>
                  {MAPPING_TYPE_LABELS[t]}
                </option>
              ))}
            </NativeSelect>
          </TableCell>
          <TableCell>
            <Input
              aria-label={`Rule for ${name}`}
              className="h-8 min-w-48"
              value={rule}
              maxLength={4000}
              onChange={(e) => setRule(e.target.value)}
            />
          </TableCell>
          <TableCell>
            <Input
              aria-label={`SQL for ${name}`}
              className={`h-8 min-w-56 ${monoClasses}`}
              value={sql}
              disabled={type === "unmapped"}
              onChange={(e) => setSql(e.target.value)}
            />
          </TableCell>
        </>
      ) : (
        <>
          <TableCell>
            <MappingState type={column.mapping_type} />
          </TableCell>
          <TableCell className="text-sand-11">
            {column.rule_text || "—"}
          </TableCell>
          <TableCell>
            {column.sql_expression ? (
              <code className={monoClasses}>{column.sql_expression}</code>
            ) : (
              "—"
            )}
          </TableCell>
        </>
      )}
      <TableCell className={`${monoClasses} text-sand-11`}>
        {inputs.length > 0 ? inputs.join(", ") : "—"}
        {errors.map((e) => (
          <p
            key={e.code}
            className="font-sans text-sm whitespace-normal text-red-11"
          >
            {e.message}
          </p>
        ))}
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      </TableCell>
      {editable && (
        <TableCell>
          <Button
            type="button"
            size="sm"
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
          </Button>
        </TableCell>
      )}
    </TableRow>
  );
}
