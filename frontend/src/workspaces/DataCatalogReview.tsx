"use client";

import { Check, ChevronDown, ChevronRight, ListFilter, Pencil, X } from "lucide-react";
import { useMemo, useState } from "react";

import {
  FIELD_LABELS,
  useAcceptAllSuggestions,
  useAcceptEditedSuggestion,
  useAcceptSuggestions,
  useCatalogSuggestions,
  useRejectSuggestions,
  type CatalogStatus,
  type CatalogSuggestion,
} from "../api/catalogSuggestions";
import { useMembers } from "../api/members";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import type { Workspace } from "../api/workspaces";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { CatalogInput, CatalogText, IconAction } from "./CatalogValue";
import {
  EmptyState,
  ErrorText,
  TableCard,
  monoClasses,
} from "./parts";

const STATUSES: readonly CatalogStatus[] = ["pending", "accepted", "rejected"];
const TITLES: Record<CatalogStatus, string> = {
  pending: "Pending",
  accepted: "Accepted",
  rejected: "Rejected",
};
// A table's own fields come before its columns.
const FIELD_ORDER = { description: 0, classification: 1, scd_hint: 2 } as const;

const plural = (n: number, one: string, many: string) =>
  `${n} ${n === 1 ? one : many}`;

/**
 * The Data catalog tab of the Source Schema: the AI's suggested descriptions, classifications
 * and SCD hints in one place, in bulk (owners and editors). Suggestions are grouped by table
 * and filtered by state and table; each is accepted, edited then accepted, or rejected, and a
 * rejected value is not suggested again.
 */
export function DataCatalogReview({
  workspace,
  systemId,
  tables,
}: {
  workspace: Workspace;
  systemId: string;
  tables: { id: string; db_schema: string; name: string }[];
}) {
  const [status, setStatus] = useState<CatalogStatus>("pending");
  // The tables the filter keeps; empty keeps every table. Filtered here, not by the server.
  const [shownTables, setShownTables] = useState<ReadonlySet<string>>(new Set());
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [editing, setEditing] = useState<{ id: string; draft: string } | null>(null);
  const filtered = shownTables.size > 0;
  const page = useCatalogSuggestions(workspace.id, systemId, status, null, true);
  const members = useMembers(workspace.id);
  const names = new Map((members.data ?? []).map((m) => [m.user_id, m.display_name]));
  const accept = useAcceptSuggestions(workspace.id, systemId);
  const acceptAll = useAcceptAllSuggestions(workspace.id, systemId);
  const reject = useRejectSuggestions(workspace.id, systemId);
  const acceptEdited = useAcceptEditedSuggestion(workspace.id, systemId);
  const busy =
    accept.isPending || acceptAll.isPending || reject.isPending || acceptEdited.isPending;
  const onError = (error: Error) => toast.error(error.message);

  const items = useMemo(
    () =>
      (page.data?.items ?? []).filter(
        (item) => !filtered || shownTables.has(item.table_id),
      ),
    [page.data, filtered, shownTables],
  );
  const schemas = useMemo(() => groupBySchema(tables), [tables]);

  // Database Schema, then table, then the table's own fields before its columns.
  const tree = useMemo(() => {
    const order = new Map(tables.map((t, i) => [t.id, i]));
    const byTable = new Map<string, CatalogSuggestion[]>();
    for (const item of items) {
      byTable.set(item.table_id, [...(byTable.get(item.table_id) ?? []), item]);
    }
    const bySchema = new Map<string, TableGroup[]>();
    for (const [id, rows] of [...byTable.entries()].sort(
      ([a], [b]) => (order.get(a) ?? 0) - (order.get(b) ?? 0),
    )) {
      const { db_schema, table } = rows[0] as CatalogSuggestion;
      bySchema.set(db_schema, [
        ...(bySchema.get(db_schema) ?? []),
        {
          id,
          name: table,
          fullName: `${db_schema}.${table}`,
          items: [...rows].sort(
            (a, b) =>
              Number(a.column != null) - Number(b.column != null) ||
              FIELD_ORDER[a.field] - FIELD_ORDER[b.field],
          ),
        },
      ]);
    }
    return [...bySchema.entries()].map(([name, tableGroups]) => ({
      name,
      tables: tableGroups,
    }));
  }, [items, tables]);

  const visibleIds = items.map((s) => s.id);
  const chosen = visibleIds.filter((id) => selected.has(id));
  const pendingView = status === "pending";

  const decided = (verb: string, count: number, skipped: number) => {
    setSelected(new Set());
    toast.success(
      `${verb} ${plural(count, "suggestion", "suggestions")}${skipped > 0 ? `; ${skipped} no longer pending` : ""}.`,
    );
  };
  const toggle = (ids: string[], on: boolean) =>
    setSelected((now) => {
      const next = new Set(now);
      for (const id of ids) {
        if (on) {
          next.add(id);
        } else {
          next.delete(id);
        }
      }
      return next;
    });
  const fold = (key: string) =>
    setCollapsed((now) => {
      const next = new Set(now);
      if (!next.delete(key)) {
        next.add(key);
      }
      return next;
    });
  const saveEdit = (item: CatalogSuggestion) => {
    const text = editing?.draft.trim() ?? "";
    if (!text) {
      return;
    }
    const done = { onSuccess: () => setEditing(null), onError };
    if (text === item.value) {
      accept.mutate([item.id], done);
    } else {
      acceptEdited.mutate({ id: item.id, value: text }, done);
    }
  };

  const counts = page.data?.counts;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          value={status}
          onValueChange={(value) => {
            setStatus(value as CatalogStatus);
            setSelected(new Set());
            setEditing(null);
          }}
        >
          <TabsList aria-label="State" className="px-0">
            {STATUSES.map((s) => (
              <TabsTrigger key={s} value={s} className="text-sm">
                {TITLES[s]}
                {counts && (
                  <span className="ms-1.5 font-normal text-subtle-foreground tabular-nums">
                    {counts[s]}
                  </span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <TableFilter
          schemas={schemas}
          shown={shownTables}
          onChange={(next) => {
            setShownTables(next);
            setSelected(new Set());
          }}
        />
        {pendingView && (
          <div className="ms-auto flex flex-wrap items-center gap-2">
            <Button
              type="button"
              disabled={busy || chosen.length === 0}
              onClick={() =>
                reject.mutate(chosen, {
                  onSuccess: (r) => decided("Rejected", r.decided.length, r.skipped.length),
                  onError,
                })
              }
            >
              <X aria-hidden />
              Reject selected{chosen.length > 0 && ` (${chosen.length})`}
            </Button>
            <Button
              type="button"
              disabled={busy || chosen.length === 0}
              onClick={() =>
                accept.mutate(chosen, {
                  onSuccess: (r) => decided("Accepted", r.decided.length, r.skipped.length),
                  onError,
                })
              }
            >
              <Check aria-hidden />
              Accept selected{chosen.length > 0 && ` (${chosen.length})`}
            </Button>
            <Button
              type="button"
              disabled={busy || visibleIds.length === 0}
              onClick={() =>
                // The filter narrows here, so a filtered view accepts what it shows.
                filtered
                  ? accept.mutate(visibleIds, {
                      onSuccess: (r) => decided("Accepted", r.decided.length, r.skipped.length),
                      onError,
                    })
                  : acceptAll.mutate(null, {
                      onSuccess: (r) => decided("Accepted", r.decided.length, r.skipped.length),
                      onError,
                    })
              }
            >
              Accept all pending
            </Button>
          </div>
        )}
      </div>
      {page.isPending && <Loading className="p-0" />}
      {page.isError && (
        <ErrorText>Could not load the suggestions: {page.error.message}</ErrorText>
      )}
      {page.isSuccess && tree.length === 0 && (
        <EmptyState>
          {pendingView
            ? "Nothing to review. Press Suggest catalog entries to fill the empty descriptions, classifications and SCD hints."
            : `No ${status} suggestions.`}
        </EmptyState>
      )}
      {tree.length > 0 && (
        <TableCard>
          <Table aria-label="Data catalog suggestions">
            <TableHeader>
              <TableRow>
                <TableHead className="w-9" />
                <TableHead>Column</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>{status === "accepted" ? "Accepted" : "Suggested"}</TableHead>
                {pendingView && <TableHead>Current</TableHead>}
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tree.flatMap((schema) => {
                const schemaKey = `s:${schema.name}`;
                const schemaIds = schema.tables.flatMap((t) => t.items.map((s) => s.id));
                const span = pendingView ? 5 : 4;
                return [
                  <GroupRow
                    key={schemaKey}
                    level={0}
                    span={span}
                    label={schema.name}
                    count={schemaIds.length}
                    open={!collapsed.has(schemaKey)}
                    onToggle={() => fold(schemaKey)}
                    select={
                      pendingView
                        ? { label: `Select all in ${schema.name}`, ids: schemaIds, selected, toggle }
                        : null
                    }
                  />,
                  ...(collapsed.has(schemaKey) ? [] : schema.tables).flatMap((group) => {
                const ids = group.items.map((s) => s.id);
                const tableKey = `t:${group.id}`;
                return [
                  <GroupRow
                    key={tableKey}
                    level={1}
                    span={span}
                    label={group.name}
                    count={ids.length}
                    open={!collapsed.has(tableKey)}
                    onToggle={() => fold(tableKey)}
                    select={
                      pendingView
                        ? { label: `Select all for ${group.fullName}`, ids, selected, toggle }
                        : null
                    }
                  />,
                  ...(collapsed.has(tableKey) ? [] : group.items).map((item) => {
                    const label = `${item.column ?? "table"} ${FIELD_LABELS[item.field].toLowerCase()}`;
                    const isEditing = editing?.id === item.id;
                    const shown = item.edited_value ?? item.value;
                    return (
                      <TableRow key={item.id}>
                        <TableCell>
                          {pendingView && (
                            <Checkbox
                              aria-label={`Select ${group.fullName} ${label}`}
                              checked={selected.has(item.id)}
                              onCheckedChange={(on) => toggle([item.id], on === true)}
                            />
                          )}
                        </TableCell>
                        <TableCell className={cn(monoClasses, "ps-14")}>
                          {item.column ?? (
                            <span className="font-sans text-subtle-foreground">(table)</span>
                          )}
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {FIELD_LABELS[item.field]}
                        </TableCell>
                        <TableCell className="min-w-64 whitespace-normal">
                          {isEditing ? (
                            <CatalogInput
                              field={item.field}
                              value={editing.draft}
                              onChange={(draft) => setEditing({ id: item.id, draft })}
                              onEnter={() => saveEdit(item)}
                              onEscape={() => setEditing(null)}
                              allowEmpty={false}
                            />
                          ) : (
                            <CatalogText field={item.field} value={shown} />
                          )}
                        </TableCell>
                        {pendingView && (
                          <TableCell className="max-w-64 whitespace-normal text-muted-foreground">
                            {item.current_value ?? "—"}
                          </TableCell>
                        )}
                        <TableCell className="text-right">
                          {pendingView ? (
                            isEditing ? (
                              <span className="inline-flex gap-1">
                                <Button
                                  type="button"
                                  size="sm"
                                  disabled={busy || !editing.draft.trim()}
                                  onClick={() => saveEdit(item)}
                                >
                                  Save and accept
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setEditing(null)}
                                >
                                  Cancel
                                </Button>
                              </span>
                            ) : (
                              <span className="inline-flex gap-0.5">
                                <IconAction
                                  label={`Accept ${label}`}
                                  disabled={busy}
                                  onClick={() =>
                                    accept.mutate([item.id], {
                                      onSuccess: (r) =>
                                        r.skipped.length > 0
                                          ? toast.error("That suggestion is no longer pending.")
                                          : undefined,
                                      onError,
                                    })
                                  }
                                >
                                  <Check aria-hidden />
                                </IconAction>
                                <IconAction
                                  label={`Edit ${label} before accepting`}
                                  disabled={busy}
                                  onClick={() => setEditing({ id: item.id, draft: item.value })}
                                >
                                  <Pencil aria-hidden />
                                </IconAction>
                                <IconAction
                                  label={`Reject ${label}`}
                                  disabled={busy}
                                  onClick={() => reject.mutate([item.id], { onError })}
                                >
                                  <X aria-hidden />
                                </IconAction>
                              </span>
                            )
                          ) : status === "accepted" ? (
                            <Badge variant="success">Accepted</Badge>
                          ) : (
                            <span className="inline-flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
                              <Badge>
                                Rejected
                                {item.decided_by && names.get(item.decided_by)
                                  ? ` by ${names.get(item.decided_by)}`
                                  : ""}
                              </Badge>
                              Won&apos;t be suggested again
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  }),
                ];
                  }),
                ];
              })}
            </TableBody>
          </Table>
        </TableCard>
      )}
      <p className="text-xs text-muted-foreground">
        Suggestions never overwrite a value someone already set, and rejected ones are not
        suggested again. Sensitivity stays with the PII folder.
      </p>
    </div>
  );
}

type TableGroup = {
  id: string;
  name: string;
  fullName: string;
  items: CatalogSuggestion[];
};

/** The tables by Database Schema, in their listed order, for the filter's groups. */
function groupBySchema(tables: { id: string; db_schema: string; name: string }[]) {
  const bySchema = new Map<string, { id: string; name: string }[]>();
  for (const t of tables) {
    bySchema.set(t.db_schema, [...(bySchema.get(t.db_schema) ?? []), t]);
  }
  return [...bySchema.entries()];
}

/** A Database Schema (level 0) or table (level 1) heading row that folds its rows away. */
function GroupRow({
  level,
  span,
  label,
  count,
  open,
  onToggle,
  select,
}: {
  level: 0 | 1;
  span: number;
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  select: {
    label: string;
    ids: string[];
    selected: ReadonlySet<string>;
    toggle: (ids: string[], on: boolean) => void;
  } | null;
}) {
  const all = select != null && select.ids.every((id) => select.selected.has(id));
  const some = select != null && select.ids.some((id) => select.selected.has(id));
  return (
    <TableRow className={level === 0 ? "bg-muted" : "bg-muted/40"}>
      <TableCell>
        {select && (
          <Checkbox
            aria-label={select.label}
            checked={all ? true : some ? "indeterminate" : false}
            onCheckedChange={(on) => select.toggle(select.ids, on === true)}
          />
        )}
      </TableCell>
      <TableCell colSpan={span}>
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className={cn(
            "inline-flex cursor-pointer items-center gap-1.5 rounded-sm text-start [&_svg]:size-3.5",
            level === 1 && "ps-6",
          )}
        >
          <ChevronRight
            aria-hidden
            className={cn("text-subtle-foreground transition-transform", open && "rotate-90")}
          />
          <span className={cn(monoClasses, level === 0 && "font-semibold")}>{label}</span>
          <span className="text-xs text-subtle-foreground tabular-nums">{count}</span>
        </button>
      </TableCell>
    </TableRow>
  );
}

/**
 * Which tables the review shows, grouped by Database Schema: a schema's box ticks or clears
 * all its tables, and nothing ticked shows every table.
 */
function TableFilter({
  schemas,
  shown,
  onChange,
}: {
  schemas: [string, { id: string; name: string }[]][];
  shown: ReadonlySet<string>;
  onChange: (next: ReadonlySet<string>) => void;
}) {
  const set = (ids: string[], on: boolean) => {
    const next = new Set(shown);
    for (const id of ids) {
      if (on) {
        next.add(id);
      } else {
        next.delete(id);
      }
    }
    onChange(next);
  };
  const keepOpen = (event: Event) => event.preventDefault();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="secondary" aria-label={`Tables: ${filterLabel(schemas, shown)}`}>
          <ListFilter aria-hidden />
          {filterLabel(schemas, shown)}
          <ChevronDown aria-hidden className="text-subtle-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-96 min-w-56 overflow-y-auto">
        <DropdownMenuCheckboxItem
          checked={shown.size === 0}
          onCheckedChange={() => onChange(new Set())}
          onSelect={keepOpen}
        >
          All tables
        </DropdownMenuCheckboxItem>
        {schemas.map(([schema, schemaTables]) => {
          const ids = schemaTables.map((t) => t.id);
          const on = ids.filter((id) => shown.has(id)).length;
          return (
            <DropdownMenuGroup key={schema}>
              <DropdownMenuSeparator />
              <DropdownMenuCheckboxItem
                checked={on === ids.length}
                onCheckedChange={() => set(ids, on < ids.length)}
                onSelect={keepOpen}
                className={cn(monoClasses, "font-semibold")}
              >
                {schema}
                {on > 0 && on < ids.length && (
                  <span className="ms-auto font-sans text-xs font-normal text-subtle-foreground tabular-nums">
                    {on}/{ids.length}
                  </span>
                )}
              </DropdownMenuCheckboxItem>
              {schemaTables.map((t) => (
                <DropdownMenuCheckboxItem
                  key={t.id}
                  checked={shown.has(t.id)}
                  onCheckedChange={(checked) => set([t.id], checked === true)}
                  onSelect={keepOpen}
                  className={cn(monoClasses, "ps-12")}
                >
                  {t.name}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuGroup>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** "All tables", one table's full name, one whole schema, or how many tables are ticked. */
function filterLabel(
  schemas: [string, { id: string; name: string }[]][],
  shown: ReadonlySet<string>,
) {
  if (shown.size === 0) {
    return "All tables";
  }
  for (const [schema, schemaTables] of schemas) {
    const ids = schemaTables.map((t) => t.id);
    if (ids.length === shown.size && ids.every((id) => shown.has(id))) {
      return schemaTables.length === 1 ? `${schema}.${schemaTables[0]?.name}` : schema;
    }
    const one = shown.size === 1 ? schemaTables.find((t) => shown.has(t.id)) : undefined;
    if (one) {
      return `${schema}.${one.name}`;
    }
  }
  return `${shown.size} tables`;
}
