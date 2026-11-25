"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/client";
import { useDwTable, useDwTables, type DwTableSummary } from "../api/dwModel";
import {
  MAPPING_TYPE_LABELS,
  TABLE_LEVEL_MAPPING_TYPES,
  layerMappingSheetUrl,
  useCoverage,
  useSaveBranchColumnMapping,
  useSaveColumnMapping,
  useTableMapping,
  type ColumnMapping,
  type MappingBranch,
  type MappingType,
  type SaveColumnMappingRequest,
  type TableMapping,
} from "../api/mappings";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { typeText } from "../lib/typeText";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { ExportSheetMenu, LayerStrip } from "./mappingParts";
import {
  EmptyState,
  ErrorText,
  Hint,
  NativeSelect,
  h3Classes,
  h5Classes,
  monoClasses,
} from "./parts";

type Layer = "core" | "mart";

interface Counts {
  total: number;
  covered: number;
  percent: number;
}

const COPY: Record<Layer, { name: string; title: string; text: string }> = {
  core: {
    name: "Core",
    title: "Core mappings",
    text: "How every Core column is built from the Staging Layer: its inputs, joins and SQL. Tables fed by several systems have one branch per system, combined with UNION ALL.",
  },
  mart: {
    name: "Mart",
    title: "Mart mappings",
    text: "How every Mart column is built from the Core Layer: aggregates, groupings and snapshots. Mart tables point at Core conformed dimensions directly, so no dimension is copied here.",
  },
};

const KIND_LABELS: Record<string, string> = {
  fact: "Fact",
  dimension: "Dimension",
  bridge: "Bridge",
  generated: "Generated",
  other: "Other",
};

/** The mapping type pill's wording; the editor's select uses the longer labels. */
const STATE_LABELS: Record<MappingType, string> = {
  ...MAPPING_TYPE_LABELS,
  lookup: "Lookup",
  system: "System",
  not_in_branch: "Not in branch",
};

const sectionLabel =
  "text-xs font-medium tracking-wide text-subtle-foreground uppercase";

/**
 * The Core or Mart Layer's Mappings page: the Layer's coverage, its tables, and each
 * table's column mappings, which editors and owners edit in place. `initialTableId`
 * preselects a table (the Model page links here with one).
 */
export function LayerMappingsPanel({
  workspace,
  layer,
  initialTableId,
}: {
  workspace: Workspace;
  layer: Layer;
  initialTableId?: string | null;
}) {
  const tables = useDwTables(workspace.id, layer);
  const coverage = useCoverage(workspace.id);
  const [chosen, setChosen] = useState<string | null>(initialTableId ?? null);
  const copy = COPY[layer];

  const layerCoverage = coverage.data?.layers.find((l) => l.layer === layer);
  const total = layerCoverage?.coverage;
  const full = total !== undefined && total.total > 0 && total.covered === total.total;

  const head = (
    <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <h3
          id="folder-title"
          className={cn(h3Classes, "flex flex-wrap items-center gap-x-2 gap-y-1")}
        >
          {copy.title}
          {total && (
            <Badge variant={full ? "success" : "warning"} dot>
              {full ? "Fully mapped" : "In progress"}
            </Badge>
          )}
        </h3>
        <p className="text-sm text-muted-foreground">{copy.text}</p>
        <ExportSheetMenu
          url={(format) => layerMappingSheetUrl(workspace.id, layer, format)}
        />
      </div>
      <LayerStrip active={layer === "core" ? ["Staging", "Core"] : ["Core", "Mart"]} />
    </header>
  );

  if (tables.isPending) {
    return <Loading />;
  }
  if (tables.isError) {
    return (
      <section aria-labelledby="folder-title" className="flex flex-col gap-5">
        {head}
        <ErrorText>Could not load the tables: {tables.error.message}</ErrorText>
      </section>
    );
  }
  const list = tables.data;
  const selected = list.find((t) => t.id === chosen) ?? list[0] ?? null;
  const byTable = new Map(layerCoverage?.tables.map((t) => [t.table_id, t.coverage]));
  const fullyMapped = layerCoverage?.tables.filter(
    (t) => t.coverage.total > 0 && t.coverage.covered === t.coverage.total,
  ).length;

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      {head}
      {coverage.isError && (
        <ErrorText>Could not load the coverage: {coverage.error.message}</ErrorText>
      )}
      {total && (
        <section
          aria-label="Coverage"
          className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-surface-raised p-4 sm:grid-cols-[auto_1fr_auto] sm:items-center sm:gap-6"
        >
          <div className="flex flex-col">
            <span className="text-3xl font-semibold tabular-nums">
              {Math.round(total.percent)}%
            </span>
            <span className="text-sm text-muted-foreground tabular-nums">
              {total.covered} of {total.total} columns mapped
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <span aria-hidden className="flex h-1.5 overflow-hidden rounded-full bg-muted">
              <span className="bg-success" style={{ width: `${total.percent}%` }} />
            </span>
            <ul className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
              <li className="flex items-center gap-1.5">
                <span aria-hidden className="size-2 rounded-full bg-success" />
                Mapped
              </li>
              <li className="flex items-center gap-1.5">
                <span aria-hidden className="size-2 rounded-full bg-muted" />
                Unmapped
              </li>
            </ul>
          </div>
          <div className="flex flex-col text-sm text-muted-foreground tabular-nums">
            <span>
              <span className="font-medium text-foreground">{list.length}</span>{" "}
              {list.length === 1 ? "table" : "tables"}
            </span>
            <span>{fullyMapped} fully mapped</span>
          </div>
        </section>
      )}
      {list.length === 0 ? (
        <EmptyState title={`No ${copy.name} tables yet`}>
          Model the Layer on the Model page. Its mappings show here once its
          tables exist.
        </EmptyState>
      ) : (
        <div className="grid min-w-0 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
          <nav aria-label={`${copy.name} tables`}>
            <ul className="flex flex-col gap-1">
              {list.map((table) => (
                <li key={table.id}>
                  <TableItem
                    table={table}
                    coverage={byTable.get(table.id)}
                    active={table.id === selected?.id}
                    onSelect={() => setChosen(table.id)}
                  />
                </li>
              ))}
            </ul>
          </nav>
          {selected && (
            <TableMappingView
              // Another table starts with no row open.
              key={selected.id}
              workspace={workspace}
              table={selected}
              covered={byTable.get(selected.id)}
            />
          )}
        </div>
      )}
    </section>
  );
}

function TableItem({
  table,
  coverage,
  active,
  onSelect,
}: {
  table: DwTableSummary;
  coverage: Counts | undefined;
  active: boolean;
  onSelect: () => void;
}) {
  const generated = table.kind === "generated";
  const complete = coverage !== undefined && coverage.percent >= 100;
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full flex-col gap-1.5 rounded-lg border px-3 py-2 text-start",
        active
          ? "border-primary bg-primary-subtle"
          : "border-border bg-surface hover:bg-muted",
      )}
    >
      <span className="flex items-center gap-2">
        <span className={cn(monoClasses, "min-w-0 flex-1 truncate font-medium")}>
          {table.name}
        </span>
        {!generated && coverage && (
          <span className="text-sm text-muted-foreground tabular-nums">
            {Math.round(coverage.percent)}%
          </span>
        )}
      </span>
      <span className="text-xs text-muted-foreground">
        {KIND_LABELS[table.kind] ?? table.kind}
        {generated && " · no mapping needed"}
      </span>
      {!generated && coverage && (
        <span aria-hidden className="flex h-1 overflow-hidden rounded-full bg-muted">
          <span
            className={complete ? "bg-success" : "bg-warning"}
            style={{ width: `${coverage.percent}%` }}
          />
        </span>
      )}
    </button>
  );
}

function TableMappingView({
  workspace,
  table,
  covered,
}: {
  workspace: Workspace;
  table: DwTableSummary;
  covered: Counts | undefined;
}) {
  const generated = table.kind === "generated";
  const heading = (
    <div className="flex flex-wrap items-center gap-2">
      <h4 className={cn(monoClasses, "text-md font-semibold")}>{table.name}</h4>
      <Badge variant="accent">{KIND_LABELS[table.kind] ?? table.kind}</Badge>
      {table.scd_type != null && <Badge>SCD {table.scd_type}</Badge>}
      {table.is_conformed && <Badge>Conformed</Badge>}
      {!generated && covered && (
        <span className="ms-auto text-sm text-muted-foreground tabular-nums">
          {covered.covered} of {covered.total} columns covered
        </span>
      )}
    </div>
  );
  return (
    <section
      aria-label={`Mappings of ${table.name}`}
      className="flex min-w-0 flex-col gap-4"
    >
      {heading}
      {generated ? (
        <EmptyState title="Generated table, no mapping needed">
          DAWAM generates this table, so its columns have no source to map.
        </EmptyState>
      ) : (
        <MappedTable workspace={workspace} tableId={table.id} />
      )}
    </section>
  );
}

function MappedTable({
  workspace,
  tableId,
}: {
  workspace: Workspace;
  tableId: string;
}) {
  const mapping = useTableMapping(workspace.id, tableId);
  const columns = useDwTable(workspace.id, tableId);
  const editable = allows(workspace, "dw_schema.edit");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [branchId, setBranchId] = useState<string | null>(null);

  if (mapping.isPending) {
    return <Loading label="Loading mappings…" />;
  }
  if (mapping.isError) {
    return (
      <ErrorText>Could not load the mappings: {mapping.error.message}</ErrorText>
    );
  }
  const m = mapping.data;
  const branch = m.branches.find((b) => b.id === branchId) ?? m.branches[0] ?? null;
  const details = new Map(columns.data?.columns.map((c) => [c.id, c]));

  return (
    <>
      <Integration mapping={m} />
      {branch && (
        <>
          <Tabs
            value={branch.id}
            onValueChange={(id) => {
              setBranchId(id);
              setOpenKey(null);
            }}
          >
            <TabsList aria-label="Branches" className="px-0">
              {m.branches.map((b) => (
                <TabsTrigger key={b.id} value={b.id} className="text-sm">
                  {b.name}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <BranchSql branch={branch} />
        </>
      )}
      <ul
        aria-label="Column mappings"
        className="divide-y divide-border overflow-hidden rounded-lg border border-border"
      >
        {(branch ? branch.columns : m.columns).map((column) => {
          const detail = details.get(column.column_id);
          const common = {
            column,
            dataType: detail ? typeText(detail.data_type) : null,
            pii: Boolean(detail?.pii_note),
            editable: editable && column.mapping_type !== "system",
            open: openKey === column.column_id,
            branched: branch !== null,
            onToggle: () =>
              setOpenKey((k) => (k === column.column_id ? null : column.column_id)),
            onClose: () => setOpenKey(null),
            onConflict: () => void mapping.refetch(),
          };
          return branch ? (
            <BranchColumnRow
              key={`${column.column_id}:${column.version}`}
              workspaceId={workspace.id}
              tableId={tableId}
              branchId={branch.id}
              {...common}
            />
          ) : (
            <TableColumnRow
              key={`${column.column_id}:${column.version}`}
              workspaceId={workspace.id}
              tableId={tableId}
              {...common}
            />
          );
        })}
      </ul>
    </>
  );
}

function Integration({ mapping }: { mapping: TableMapping }) {
  if (!mapping.integration_rule && mapping.match_keys.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-1 text-sm">
      {mapping.integration_rule && (
        <p>
          <span className="font-medium">Integration rule</span>{" "}
          <span className="text-muted-foreground">{mapping.integration_rule}</span>
        </p>
      )}
      {mapping.match_keys.length > 0 && (
        <p>
          <span className="font-medium">Match keys</span>{" "}
          <span className={cn(monoClasses, "text-muted-foreground")}>
            {mapping.match_keys.join(", ")}
          </span>
        </p>
      )}
    </div>
  );
}

function BranchSql({ branch }: { branch: MappingBranch }) {
  const parts: [string, string | null][] = [
    ["Driving table", branch.driving_input],
    ["Joins", branch.joins],
    ["Filters", branch.filters],
    ["Group by", branch.group_by],
    ["Having", branch.having],
  ];
  const shown = parts.filter(([, value]) => value);
  if (shown.length === 0) {
    return null;
  }
  return (
    <dl className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
      {shown.map(([label, value]) => (
        <div key={label} className="flex flex-col gap-0.5">
          <dt className={sectionLabel}>{label}</dt>
          <dd>
            <code className={cn(monoClasses, "break-words whitespace-pre-wrap")}>
              {value}
            </code>
          </dd>
        </div>
      ))}
    </dl>
  );
}

interface RowProps {
  column: ColumnMapping;
  dataType: string | null;
  pii: boolean;
  editable: boolean;
  open: boolean;
  branched: boolean;
  onToggle: () => void;
  onClose: () => void;
  onConflict: () => void;
}

interface Save {
  mutate: (
    body: SaveColumnMappingRequest,
    options: { onSuccess: () => void; onError: (error: Error) => void },
  ) => void;
  isPending: boolean;
  error: Error | null;
}

function TableColumnRow(props: RowProps & { workspaceId: string; tableId: string }) {
  const save = useSaveColumnMapping(
    props.workspaceId,
    props.tableId,
    props.column.column_id,
  );
  return <ColumnRow {...props} save={save} />;
}

function BranchColumnRow(
  props: RowProps & { workspaceId: string; tableId: string; branchId: string },
) {
  const save = useSaveBranchColumnMapping(
    props.workspaceId,
    props.tableId,
    props.branchId,
    props.column.column_id,
  );
  return <ColumnRow {...props} save={save} />;
}

/** What the "From" cell says for a column: its SQL, lookup, or why there is none. */
function fromText(column: ColumnMapping): string {
  if (column.mapping_type === "not_in_branch") {
    return "NULL";
  }
  if (column.mapping_type === "system") {
    return "Generated by DAWAM";
  }
  if (column.mapping_type === "lookup" && column.lookup) {
    return `Lookup ${column.lookup.dimension_name} on ${column.lookup.nk_inputs.join(", ")}`;
  }
  return column.sql_expression || "Not mapped yet";
}

function ColumnRow({
  column,
  dataType,
  pii,
  editable,
  open,
  branched,
  onToggle,
  onClose,
  onConflict,
  save,
}: RowProps & { save: Save }) {
  const [type, setType] = useState<MappingType>(column.mapping_type);
  const [rule, setRule] = useState(column.rule_text);
  const [sql, setSql] = useState(column.sql_expression);
  const name = column.column_name;
  const unmapped = column.mapping_type === "unmapped";
  const noSql = type === "unmapped" || type === "not_in_branch";
  const options: MappingType[] = [
    ...TABLE_LEVEL_MAPPING_TYPES,
    ...(branched ? (["not_in_branch"] as const) : []),
  ];
  if (!options.includes(column.mapping_type)) {
    options.push(column.mapping_type);
  }
  const inputs = [...column.inputs, ...column.uses].map(
    (i) => `${i.table_name}.${i.column_name}`,
  );

  function submit() {
    const lookup = column.lookup;
    save.mutate(
      {
        mapping_type: type,
        rule_text: rule,
        sql_expression: noSql ? "" : sql,
        version: column.version,
        ...(type === "lookup" && lookup
          ? {
              lookup: {
                nk_inputs: lookup.nk_inputs,
                as_of_input: lookup.as_of_input ?? null,
                unknown_key: lookup.unknown_key,
              },
            }
          : {}),
      },
      {
        onSuccess: onClose,
        // A stale version: reload the mapping and keep the row open with what was typed.
        onError: (error) => {
          if (error instanceof ApiError && error.code === "version_conflict") {
            onConflict();
          }
        },
      },
    );
  }

  const summary = (
    <>
      <span className="flex min-w-0 flex-wrap items-center gap-2">
        <span className={cn(monoClasses, "font-medium")}>{name}</span>
        {dataType && (
          <span className={cn(monoClasses, "text-muted-foreground")}>{dataType}</span>
        )}
        {pii && <Badge variant="danger">PII</Badge>}
      </span>
      <Badge variant={unmapped ? "warning" : "success"} dot>
        {STATE_LABELS[column.mapping_type]}
      </Badge>
      <span className="min-w-0 basis-full sm:flex-1 sm:basis-auto">
        <span
          className={cn(
            "block break-words whitespace-pre-wrap",
            column.sql_expression || column.lookup
              ? monoClasses
              : "text-sm text-muted-foreground",
          )}
        >
          {fromText(column)}
        </span>
        {column.validation.errors.map((e) => (
          <span key={e.code} className="block text-sm text-danger-strong">
            {e.message}
          </span>
        ))}
      </span>
    </>
  );
  const rowClasses = "flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm";

  return (
    <li className={cn(unmapped && "bg-warning-subtle")}>
      {editable ? (
        <button
          type="button"
          aria-expanded={open}
          aria-label={`Edit mapping of ${name}`}
          onClick={onToggle}
          className={cn(rowClasses, "w-full text-start hover:bg-muted")}
        >
          {summary}
          <ChevronDown
            aria-hidden
            className={cn("size-4 text-subtle-foreground", !open && "-rotate-90")}
          />
        </button>
      ) : (
        <div className={rowClasses}>{summary}</div>
      )}
      {editable && open && (
        <form
          aria-label={`Mapping of ${name}`}
          className="flex flex-col gap-3 border-t border-border bg-surface px-3 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <label className="flex max-w-xs flex-col gap-1 text-sm font-medium">
            Mapping type
            <NativeSelect
              value={type}
              onChange={(e) => setType(e.target.value as MappingType)}
            >
              {options.map((t) => (
                <option key={t} value={t}>
                  {MAPPING_TYPE_LABELS[t]}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            Rule
            <Input
              value={rule}
              maxLength={4000}
              onChange={(e) => setRule(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            SQL expression
            <Input
              className={monoClasses}
              value={sql}
              disabled={noSql}
              onChange={(e) => setSql(e.target.value)}
            />
          </label>
          <div className="flex flex-col gap-1">
            <h5 className={h5Classes}>Inputs read from the SQL</h5>
            {inputs.length > 0 ? (
              <p className={cn(monoClasses, "text-muted-foreground")}>
                {inputs.join(", ")}
              </p>
            ) : (
              <Hint>None yet. They are read from the SQL when you save.</Hint>
            )}
          </div>
          {save.error && <ErrorText>{save.error.message}</ErrorText>}
          <div className="flex gap-2">
            <Button type="submit" disabled={save.isPending}>
              Save mapping
            </Button>
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}
