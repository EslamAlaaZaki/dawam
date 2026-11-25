"use client";

import { CalendarDays, Folder, Pencil, Table2 } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

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
  useValidation,
  type ColumnMapping,
  type MappingBranch,
  type MappingType,
  type SaveColumnMappingRequest,
  type TableMapping,
} from "../api/mappings";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "../components/ui/dialog";
import { Textarea } from "../components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import { typeText } from "../lib/typeText";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import {
  ExportSheetMenu,
  LayerStrip,
  MappingsExplorer,
  StateDot,
} from "./mappingParts";
import { TreeGroup, TreeLeaf } from "./SchemaBrowser";
import {
  EmptyState,
  ErrorText,
  h3Classes,
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

const KIND_PLURALS: Record<string, string> = {
  fact: "Facts",
  dimension: "Dimensions",
  bridge: "Bridges",
  aggregate: "Aggregates",
  generated: "Generated",
  other: "Other",
};

const DOTS = {
  full: { label: "Fully mapped", className: "bg-success" },
  partial: { label: "Partly mapped", className: "bg-warning" },
  generated: { label: "Generated", className: "bg-border-strong" },
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
  const validation = useValidation(workspace.id);
  const [chosen, setChosen] = useState<string | null>(initialTableId ?? null);
  const [search, setSearch] = useState("");
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const copy = COPY[layer];

  const layerCoverage = coverage.data?.layers.find((l) => l.layer === layer);
  const total = layerCoverage?.coverage;
  const full =
    total !== undefined && total.total > 0 && total.covered === total.total;

  const head = (
    <header className="flex flex-col gap-3 border-b border-border pb-5 sm:flex-row sm:items-start">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <h3
          id="folder-title"
          className={cn(
            h3Classes,
            "flex flex-wrap items-center gap-x-2 gap-y-1",
          )}
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
      <LayerStrip
        active={layer === "core" ? ["Staging", "Core"] : ["Core", "Mart"]}
      />
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
  const byTable = new Map(
    layerCoverage?.tables.map((t) => [t.table_id, t.coverage]),
  );
  const fullyMapped = layerCoverage?.tables.filter(
    (t) => t.coverage.total > 0 && t.coverage.covered === t.coverage.total,
  ).length;
  // As in the prototype, the Layer card counts every column: system columns are DAWAM's,
  // so always covered, on top of the mappable ones the API counts.
  const system = total?.system ?? 0;
  const allColumns = (total?.total ?? 0) + system;
  const allCovered = (total?.covered ?? 0) + system;
  const segments = [
    { label: "mapped", count: total?.covered ?? 0, className: "bg-success" },
    { label: "system", count: system, className: "bg-subtle-foreground" },
    {
      label: "unmapped",
      count: (total?.total ?? 0) - (total?.covered ?? 0),
      className: "bg-warning",
    },
  ];
  const multiSystem =
    layerCoverage?.tables.filter((t) => t.branch_count >= 2).length ?? 0;
  const aggregates = list.filter((t) => t.is_aggregate).length;
  const typeWarnings = validation.data?.problems.filter(
    (p) => p.layer === layer && TYPE_WARNINGS.has(p.code),
  ).length;

  const needle = search.trim().toLowerCase();
  const searching = needle !== "";
  const shown = list.filter((t) => t.name.toLowerCase().includes(needle));
  // The chosen table, or the first one the search leaves.
  const selected = shown.find((t) => t.id === chosen) ?? shown[0] ?? null;
  const groups: [string, DwTableSummary[]][] = [];
  for (const table of shown) {
    const group = groups.find(([kind]) => kind === table.kind);
    if (group) {
      group[1].push(table);
    } else {
      groups.push([table.kind, [table]]);
    }
  }
  const legend = [
    DOTS.full,
    DOTS.partial,
    ...(list.some((t) => t.kind === "generated") ? [DOTS.generated] : []),
  ];

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-6">
      {head}
      {coverage.isError && (
        <ErrorText>
          Could not load the coverage: {coverage.error.message}
        </ErrorText>
      )}
      {total && (
        <section
          aria-label="Coverage"
          className="grid grid-cols-1 items-center gap-6 rounded-lg border border-border bg-surface-raised p-5 sm:grid-cols-2 xl:grid-cols-[minmax(16rem,1.4fr)_repeat(3,minmax(8.75rem,1fr))]"
        >
          <div className="flex flex-col gap-2.5 sm:col-span-2 xl:col-span-1">
            <span className="text-xs font-medium text-subtle-foreground">
              Layer coverage
            </span>
            <span className="text-3xl font-semibold tabular-nums">
              {allColumns > 0 ? Math.floor((100 * allCovered) / allColumns) : 0}
              %{" "}
              <span className="text-base font-normal text-muted-foreground">
                {allCovered} of {allColumns} columns
              </span>
            </span>
            <span
              aria-hidden
              className="flex h-2 gap-0.5 overflow-hidden rounded-sm"
            >
              {segments
                .filter(({ count }) => count > 0)
                .map(({ label, count, className }) => (
                  <span
                    key={label}
                    className={className}
                    style={{ flexGrow: count }}
                  />
                ))}
            </span>
            <ul className="flex flex-wrap gap-4 text-xs text-muted-foreground tabular-nums">
              {segments.map(({ label, count, className }) => (
                <li key={label} className="flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className={cn("size-2 rounded-xs", className)}
                  />
                  {count} {label}
                </li>
              ))}
            </ul>
          </div>
          <Tile label={`${copy.name} tables`} value={list.length}>
            {fullyMapped ?? 0} fully mapped
          </Tile>
          {layer === "core" ? (
            <Tile label="Multi-system" value={multiSystem}>
              tables with 2+ branches
            </Tile>
          ) : (
            <Tile label="Aggregates" value={aggregates}>
              with GROUP BY
            </Tile>
          )}
          <Tile
            label="Warnings"
            value={typeWarnings ?? "–"}
            className={typeWarnings ? "text-warning-strong" : undefined}
          >
            type may not fit
          </Tile>
        </section>
      )}
      {list.length === 0 ? (
        <EmptyState title={`No ${copy.name} tables yet`}>
          Model the Layer on the Model page. Its mappings show here once its
          tables exist.
        </EmptyState>
      ) : (
        <MappingsExplorer
          idPrefix={`${layer}-mappings`}
          placeholder="A table"
          treeLabel={`${copy.name} tables`}
          search={search}
          onSearch={setSearch}
          empty={shown.length === 0}
          legend={legend}
          tree={groups.map(([kind, items]) => (
            <TreeGroup
              key={kind}
              label={KIND_PLURALS[kind] ?? `${kind}s`}
              icon={<Folder />}
              count={items.length}
              level={0}
              open={searching || !closed.has(kind)}
              onToggle={() =>
                setClosed((c) => {
                  const next = new Set(c);
                  if (!next.delete(kind)) {
                    next.add(kind);
                  }
                  return next;
                })
              }
            >
              {items.map((table) => (
                <TableLeaf
                  key={table.id}
                  table={table}
                  coverage={byTable.get(table.id)}
                  active={table.id === selected?.id}
                  onSelect={() => setChosen(table.id)}
                />
              ))}
            </TreeGroup>
          ))}
        >
          {!selected && (
            <EmptyState className="m-5" title="No table matches." />
          )}
          {selected && (
            <TableMappingView
              // Another table starts with no row open.
              key={selected.id}
              workspace={workspace}
              table={selected}
              covered={byTable.get(selected.id)}
            />
          )}
        </MappingsExplorer>
      )}
    </section>
  );
}

/** Validation warnings that a value's data type may not fit its target column. */
const TYPE_WARNINGS = new Set([
  "may_truncate",
  "may_lose_precision",
  "may_fail_conversion",
  "nullable_into_required",
]);

/** One figure of the Coverage card, set off by a rule on wide screens. */
function Tile({
  label,
  value,
  className,
  children,
}: {
  label: string;
  value: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex flex-col gap-1 xl:border-s xl:border-border xl:ps-6"
    >
      <span className="text-xs font-medium text-subtle-foreground">
        {label}
      </span>
      <span className={cn("text-2xl font-semibold tabular-nums", className)}>
        {value}
      </span>
      <span className="text-sm text-subtle-foreground">{children}</span>
    </div>
  );
}

function TableLeaf({
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
  const state = generated
    ? DOTS.generated
    : coverage
      ? coverage.percent >= 100
        ? DOTS.full
        : DOTS.partial
      : null;
  return (
    <TreeLeaf
      name={table.name}
      title={table.name}
      icon={<Table2 />}
      level={1}
      selected={active}
      onOpen={onSelect}
      after={
        <>
          {!generated && coverage && (
            <span className="text-xs text-subtle-foreground tabular-nums">
              {Math.round(coverage.percent)}%
            </span>
          )}
          {state && (
            <StateDot label={state.label} className={state.className} />
          )}
        </>
      }
    />
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
  const header = (extra?: ReactNode) => (
    <div className="flex flex-col gap-3 border-b border-border px-5 py-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <h4 className={cn(monoClasses, "text-xl font-medium")}>{table.name}</h4>
        <Badge>{KIND_LABELS[table.kind] ?? table.kind}</Badge>
        {table.scd_type != null && <Badge>SCD {table.scd_type}</Badge>}
        {table.is_conformed && <Badge>Conformed</Badge>}
        {!generated && covered && (
          <span className="ms-auto text-sm text-muted-foreground tabular-nums">
            {covered.covered} of {covered.total} columns covered
          </span>
        )}
      </div>
      {extra}
    </div>
  );
  return (
    <section
      aria-label={`Mappings of ${table.name}`}
      className="flex min-w-0 flex-col"
    >
      {generated ? (
        <>
          {header()}
          <div className="flex flex-col items-center gap-2 px-5 py-10 text-center">
            <CalendarDays
              aria-hidden
              className="size-5 text-muted-foreground"
            />
            <p className="text-md font-medium">
              Generated table, no mapping needed
            </p>
            <p className="max-w-md text-sm text-muted-foreground">
              DAWAM generates this table, so its columns have no source to map.
            </p>
          </div>
        </>
      ) : (
        <MappedTable
          workspace={workspace}
          tableId={table.id}
          tableName={table.name}
          header={header}
        />
      )}
    </section>
  );
}

function MappedTable({
  workspace,
  tableId,
  tableName,
  header,
}: {
  workspace: Workspace;
  tableId: string;
  tableName: string;
  header: (extra?: ReactNode) => ReactNode;
}) {
  const mapping = useTableMapping(workspace.id, tableId);
  const columns = useDwTable(workspace.id, tableId);
  const editable = allows(workspace, "dw_schema.edit");
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [branchId, setBranchId] = useState<string | null>(null);

  if (mapping.isPending) {
    return (
      <>
        {header()}
        <Loading label="Loading mappings…" />
      </>
    );
  }
  if (mapping.isError) {
    return (
      <>
        {header()}
        <div className="p-5">
          <ErrorText>
            Could not load the mappings: {mapping.error.message}
          </ErrorText>
        </div>
      </>
    );
  }
  const m = mapping.data;
  const branch =
    m.branches.find((b) => b.id === branchId) ?? m.branches[0] ?? null;
  const details = new Map(columns.data?.columns.map((c) => [c.id, c]));

  return (
    <>
      {header(<Integration mapping={m} />)}
      {branch && (
        <>
          <Tabs
            value={branch.id}
            onValueChange={(id) => {
              setBranchId(id);
              setOpenKey(null);
            }}
          >
            <TabsList aria-label="Branches" className="flex-wrap gap-5 px-5">
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
      <div className="overflow-x-auto">
        <div className="min-w-160">
          <div
            aria-hidden
            className={cn(
              rowGrid,
              "border-b border-border px-5 py-2.5 text-xs font-medium text-muted-foreground",
            )}
          >
            <span>Column</span>
            <span>Mapping</span>
            <span>From</span>
            <span />
          </div>
          <ul aria-label="Column mappings">
            {(branch ? branch.columns : m.columns).map((column) => {
              const detail = details.get(column.column_id);
              const common = {
                column,
                dataType: detail ? typeText(detail.data_type) : null,
                pii: Boolean(detail?.pii_note),
                editable: editable && column.mapping_type !== "system",
                open: openKey === column.column_id,
                branched: branch !== null,
                tableName,
                scope: branch
                  ? /branch$/i.test(branch.name)
                    ? branch.name
                    : `${branch.name} branch`
                  : "Table mapping",
                onToggle: () =>
                  setOpenKey((k) =>
                    k === column.column_id ? null : column.column_id,
                  ),
                onClose: () => setOpenKey(null),
                onConflict: () => void mapping.refetch(),
              };
              return branch ? (
                <BranchColumnRow
                  key={`${branch.id}:${column.column_id}`}
                  workspaceId={workspace.id}
                  tableId={tableId}
                  branchId={branch.id}
                  {...common}
                />
              ) : (
                <TableColumnRow
                  key={`table:${column.column_id}`}
                  workspaceId={workspace.id}
                  tableId={tableId}
                  {...common}
                />
              );
            })}
          </ul>
        </div>
      </div>
    </>
  );
}

/** The integration rule and match keys of a table fed by several branches (read-only). */
function Integration({ mapping }: { mapping: TableMapping }) {
  if (!mapping.integration_rule && mapping.match_keys.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
      {mapping.integration_rule && (
        <p className="flex min-w-0 flex-1 basis-80 gap-6">
          <span className="shrink-0 text-subtle-foreground">
            Integration rule
          </span>
          <span>{mapping.integration_rule}</span>
        </p>
      )}
      {mapping.match_keys.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5">
          <span className="text-subtle-foreground">
            {mapping.match_keys.length === 1 ? "Match key" : "Match keys"}
          </span>
          {mapping.match_keys.map((key) => (
            <Badge key={key} variant="accent" className={monoClasses}>
              {key}
            </Badge>
          ))}
        </p>
      )}
    </div>
  );
}

/** The branch's query around the column expressions: FROM, joins, WHERE, GROUP BY, HAVING. */
function BranchSql({ branch }: { branch: MappingBranch }) {
  const lines = [
    branch.driving_input && `FROM ${branch.driving_input}`,
    branch.joins,
    branch.filters && `WHERE ${branch.filters}`,
    branch.group_by && `GROUP BY ${branch.group_by}`,
    branch.having && `HAVING ${branch.having}`,
  ].filter(Boolean);
  if (lines.length === 0) {
    return null;
  }
  return (
    <pre
      aria-label="Branch SQL"
      className={cn(
        monoClasses,
        "border-b border-border bg-surface px-5 py-3 text-xs break-words whitespace-pre-wrap text-muted-foreground",
      )}
    >
      {lines.join("\n")}
    </pre>
  );
}

/** The column grid: column, mapping type, from, the edit mark. */
const rowGrid =
  "grid grid-cols-[minmax(0,1fr)_6.5rem_minmax(0,1.6fr)_1rem] gap-3";

/** The mapping type chip's dot in the editor. */
const KIND_DOT: Record<MappingType, string> = {
  direct: "bg-muted-foreground",
  derived: "bg-primary",
  constant: "bg-subtle-foreground",
  lookup: "bg-info",
  not_in_branch: "bg-border-strong",
  unmapped: "bg-warning",
  system: "bg-border-strong",
};

/** One line under the type chips saying what the chosen type does. */
const KIND_HINTS: Record<MappingType, string> = {
  direct: "Copy one input column as it is.",
  derived: "Compute the value with a SQL expression over one or more inputs.",
  constant: "Use the same fixed value for every row.",
  lookup: "Find the dimension's key from the natural key in the SQL.",
  not_in_branch: "This branch has no value for the column, so it loads NULL.",
  unmapped: "Not mapped yet. The column counts as not covered.",
  system: "DAWAM generates this column.",
};

/** The mapping type pill's tint per type. */
const KIND_BADGE: Partial<Record<MappingType, string>> = {
  derived: "bg-primary-subtle text-primary",
  lookup: "bg-info-subtle text-info-strong",
  unmapped: "bg-warning-subtle text-warning-strong",
  system:
    "bg-transparent text-muted-foreground ring-1 ring-border-strong ring-inset",
  not_in_branch:
    "bg-transparent text-muted-foreground ring-1 ring-border ring-inset",
};

interface RowProps {
  column: ColumnMapping;
  dataType: string | null;
  pii: boolean;
  editable: boolean;
  open: boolean;
  branched: boolean;
  /** The table's name, and which mapping is edited: a branch's or the table's. */
  tableName: string;
  scope: string;
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

function TableColumnRow(
  props: RowProps & { workspaceId: string; tableId: string },
) {
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
    return `${column.lookup.dimension_name} on ${column.lookup.nk_inputs.join(", ")}`;
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
  tableName,
  scope,
  onToggle,
  onClose,
  onConflict,
  save,
}: RowProps & { save: Save }) {
  const fieldId = useId();
  const [type, setType] = useState<MappingType>(column.mapping_type);
  const [rule, setRule] = useState(column.rule_text);
  const [sql, setSql] = useState(column.sql_expression);
  // The draft is seeded from the saved mapping each time the row opens, and survives a
  // refetch (a version conflict) while it stays open.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) {
      setType(column.mapping_type);
      setRule(column.rule_text);
      setSql(column.sql_expression);
    }
  }
  const name = column.column_name;
  const kind = column.mapping_type;
  const unmapped = kind === "unmapped";
  const noSql = type === "unmapped" || type === "not_in_branch";
  const options: MappingType[] = [
    ...TABLE_LEVEL_MAPPING_TYPES,
    ...(branched ? (["not_in_branch"] as const) : []),
  ];
  if (!options.includes(kind)) {
    options.push(kind);
  }
  const inputs = [...column.inputs, ...column.uses].map(
    (i) => `${i.table_name}.${i.column_name}`,
  );
  const plainFrom = unmapped || kind === "system";
  const sqlNote = noSql
    ? "not used for this type"
    : type === "lookup"
      ? "the natural key to look up"
      : null;

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

  const cells = (
    <>
      <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
        <span className={cn(monoClasses, "font-medium break-all")}>{name}</span>
        {dataType && (
          <span className={cn(monoClasses, "text-xs text-muted-foreground")}>
            {dataType}
          </span>
        )}
        {pii && (
          <Badge variant="danger" className="self-center">
            PII
          </Badge>
        )}
      </span>
      <span className="flex">
        <Badge className={KIND_BADGE[kind]}>{STATE_LABELS[kind]}</Badge>
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span
          className={cn(
            "break-words whitespace-pre-wrap",
            plainFrom ? "text-sm" : cn(monoClasses, "text-xs"),
            unmapped
              ? "text-warning-strong"
              : kind === "system" || kind === "not_in_branch"
                ? "text-muted-foreground"
                : "text-foreground",
          )}
        >
          {fromText(column)}
        </span>
        {column.validation.errors.map((e) => (
          <span key={e.code} className="text-xs text-warning-strong">
            {e.message}
          </span>
        ))}
      </span>
      {editable ? (
        <Pencil
          aria-hidden
          className={cn(
            "size-3.5",
            open ? "text-primary" : "text-subtle-foreground",
          )}
        />
      ) : (
        <span />
      )}
    </>
  );
  const rowClasses = cn(rowGrid, "min-h-10 items-center px-5 py-1.5 text-sm");

  return (
    <li
      className={cn(
        "border-s-2 border-b border-b-muted",
        open ? "border-s-primary bg-primary-subtle" : "border-s-transparent",
        !open && unmapped && "bg-warning-subtle",
      )}
    >
      {editable ? (
        <button
          type="button"
          aria-haspopup="dialog"
          aria-label={`Edit mapping of ${name}`}
          onClick={onToggle}
          className={cn(
            rowClasses,
            "w-full cursor-pointer text-start",
            !open && "hover:bg-muted",
          )}
        >
          {cells}
        </button>
      ) : (
        <div className={rowClasses}>{cells}</div>
      )}
      {editable && (
        <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
          <DialogContent className="w-full max-w-2xl gap-0 overflow-hidden p-0">
            <form
              aria-label={`Mapping of ${name}`}
              className="flex min-h-0 flex-col"
              onSubmit={(e) => {
                e.preventDefault();
                submit();
              }}
            >
              <div className="flex flex-col gap-1 border-b border-border px-5 pt-4 pb-3.5 pe-12">
                <DialogTitle className="text-lg font-semibold">
                  Edit mapping
                </DialogTitle>
                <DialogDescription className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
                  <span
                    className={cn(monoClasses, "font-medium text-foreground")}
                  >
                    {tableName}.{name}
                  </span>
                  {dataType && <span className={monoClasses}>{dataType}</span>}
                  <span aria-hidden>·</span>
                  <span>{scope}</span>
                </DialogDescription>
              </div>
              <div className="flex min-h-0 flex-col gap-4.5 overflow-y-auto px-5 py-4.5">
                <div className="flex flex-col gap-2">
                  <span
                    id={`${fieldId}-kind`}
                    className="text-xs font-medium text-muted-foreground"
                  >
                    Mapping type
                  </span>
                  <div
                    role="radiogroup"
                    aria-labelledby={`${fieldId}-kind`}
                    className="flex flex-wrap gap-1.5"
                  >
                    {options.map((t) => (
                      <button
                        key={t}
                        type="button"
                        role="radio"
                        aria-checked={type === t}
                        onClick={() => setType(t)}
                        className={cn(
                          "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full px-3 text-sm font-medium ring-1 transition-colors ring-inset",
                          type === t
                            ? "bg-primary-subtle text-primary ring-primary"
                            : "bg-surface-raised text-muted-foreground ring-border hover:text-foreground",
                        )}
                      >
                        <span
                          aria-hidden
                          className={cn("size-1.5 rounded-full", KIND_DOT[t])}
                        />
                        {STATE_LABELS[t]}
                      </button>
                    ))}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {KIND_HINTS[type]}
                  </span>
                </div>
                <div className="flex flex-col gap-2">
                  <span className="flex items-baseline gap-2 text-xs font-medium text-muted-foreground">
                    <label htmlFor={`${fieldId}-sql`}>SQL expression</label>
                    {sqlNote && (
                      <span className="font-normal text-subtle-foreground">
                        {sqlNote}
                      </span>
                    )}
                  </span>
                  <div
                    className={cn(
                      "grid grid-cols-[2.25rem_minmax(0,1fr)] overflow-hidden rounded-lg border",
                      noSql
                        ? "border-border bg-surface"
                        : "border-control-edge bg-surface-raised focus-within:border-primary",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        monoClasses,
                        "border-e border-border bg-surface py-2.5 text-center text-xs text-subtle-foreground",
                      )}
                    >
                      fx
                    </span>
                    <textarea
                      id={`${fieldId}-sql`}
                      rows={2}
                      value={noSql ? "" : sql}
                      disabled={noSql}
                      placeholder={noSql ? "NULL" : "alias.column"}
                      onChange={(e) => setSql(e.target.value)}
                      className={cn(
                        monoClasses,
                        "min-h-10 resize-y bg-transparent px-3 py-2.5 outline-none disabled:cursor-not-allowed disabled:text-subtle-foreground",
                      )}
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    <span>Reads</span>
                    {!noSql && inputs.length > 0 ? (
                      inputs.map((input) => (
                        <span
                          key={input}
                          className={cn(
                            monoClasses,
                            "rounded-md bg-muted px-2 text-xs text-foreground",
                          )}
                        >
                          {input}
                        </span>
                      ))
                    ) : (
                      <span className="text-subtle-foreground">
                        nothing yet, the inputs are read from the SQL when you
                        save
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <span className="flex items-baseline gap-2 text-xs font-medium text-muted-foreground">
                    <label htmlFor={`${fieldId}-rule`}>Rule</label>
                    <span className="font-normal text-subtle-foreground">
                      what the column means, in plain language
                    </span>
                  </span>
                  <Textarea
                    id={`${fieldId}-rule`}
                    rows={2}
                    value={rule}
                    maxLength={4000}
                    placeholder="For example: the customer's full name, first name then last name"
                    onChange={(e) => setRule(e.target.value)}
                  />
                </div>
                {save.error && <ErrorText>{save.error.message}</ErrorText>}
              </div>
              <div className="flex flex-wrap items-center gap-2 border-t border-border bg-surface px-5 py-3">
                <span className="text-xs text-muted-foreground">
                  {branched
                    ? "Only this column's mapping changes. The branch's joins and filters stay as they are."
                    : "Only this column's mapping changes."}
                </span>
                <span className="flex-1" />
                <Button type="button" variant="ghost" onClick={onClose}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={save.isPending}
                >
                  Save mapping
                </Button>
              </div>
            </form>
          </DialogContent>
        </Dialog>
      )}
    </li>
  );
}
