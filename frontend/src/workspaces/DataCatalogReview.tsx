"use client";

import { Check, Pencil, X } from "lucide-react";
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
import { Loading } from "../shell/Loading";
import { CatalogInput, CatalogText, IconAction } from "./CatalogValue";
import {
  EmptyState,
  ErrorText,
  NativeSelect,
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
  const [tableId, setTableId] = useState("");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [editing, setEditing] = useState<{ id: string; draft: string } | null>(null);
  const page = useCatalogSuggestions(
    workspace.id,
    systemId,
    status,
    tableId || null,
    true,
  );
  const members = useMembers(workspace.id);
  const names = new Map((members.data ?? []).map((m) => [m.user_id, m.display_name]));
  const accept = useAcceptSuggestions(workspace.id, systemId);
  const acceptAll = useAcceptAllSuggestions(workspace.id, systemId);
  const reject = useRejectSuggestions(workspace.id, systemId);
  const acceptEdited = useAcceptEditedSuggestion(workspace.id, systemId);
  const busy =
    accept.isPending || acceptAll.isPending || reject.isPending || acceptEdited.isPending;
  const onError = (error: Error) => toast.error(error.message);

  const groups = useMemo(() => {
    const order = new Map(tables.map((t, i) => [t.id, i]));
    const byTable = new Map<string, { name: string; items: CatalogSuggestion[] }>();
    for (const item of page.data?.items ?? []) {
      const group = byTable.get(item.table_id) ?? {
        name: `${item.db_schema}.${item.table}`,
        items: [],
      };
      group.items.push(item);
      byTable.set(item.table_id, group);
    }
    return [...byTable.entries()]
      .sort(([a], [b]) => (order.get(a) ?? 0) - (order.get(b) ?? 0))
      .map(([id, { name, items }]) => ({
        id,
        name,
        items: [...items].sort(
          (a, b) =>
            Number(a.column != null) - Number(b.column != null) ||
            FIELD_ORDER[a.field] - FIELD_ORDER[b.field],
        ),
      }));
  }, [page.data, tables]);

  const visibleIds = (page.data?.items ?? []).map((s) => s.id);
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
        <NativeSelect
          aria-label="Table"
          className="w-auto min-w-44"
          value={tableId}
          onChange={(event) => {
            setTableId(event.target.value);
            setSelected(new Set());
          }}
        >
          <option value="">All tables</option>
          {tables.map((t) => (
            <option key={t.id} value={t.id}>
              {t.db_schema}.{t.name}
            </option>
          ))}
        </NativeSelect>
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
                acceptAll.mutate(tableId || null, {
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
      {page.isSuccess && groups.length === 0 && (
        <EmptyState>
          {pendingView
            ? "Nothing to review. Press Suggest catalog entries to fill the empty descriptions, classifications and SCD hints."
            : `No ${status} suggestions.`}
        </EmptyState>
      )}
      {groups.length > 0 && (
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
              {groups.map((group) => {
                const ids = group.items.map((s) => s.id);
                const all = ids.every((id) => selected.has(id));
                const some = ids.some((id) => selected.has(id));
                return [
                  <TableRow key={`g:${group.id}`} className="bg-muted/50">
                    <TableCell>
                      {pendingView && (
                        <Checkbox
                          aria-label={`Select all for ${group.name}`}
                          checked={all ? true : some ? "indeterminate" : false}
                          onCheckedChange={(on) => toggle(ids, on === true)}
                        />
                      )}
                    </TableCell>
                    <TableCell colSpan={pendingView ? 5 : 4}>
                      <span className={monoClasses}>{group.name}</span>
                      <span className="ms-2 text-xs text-subtle-foreground tabular-nums">
                        {group.items.length}
                      </span>
                    </TableCell>
                  </TableRow>,
                  ...group.items.map((item) => {
                    const label = `${item.column ?? "table"} ${FIELD_LABELS[item.field].toLowerCase()}`;
                    const isEditing = editing?.id === item.id;
                    const shown = item.edited_value ?? item.value;
                    return (
                      <TableRow key={item.id}>
                        <TableCell>
                          {pendingView && (
                            <Checkbox
                              aria-label={`Select ${group.name} ${label}`}
                              checked={selected.has(item.id)}
                              onCheckedChange={(on) => toggle([item.id], on === true)}
                            />
                          )}
                        </TableCell>
                        <TableCell className={monoClasses}>
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
