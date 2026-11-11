"use client";

import { ArrowRight, Search } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  useAcceptedRelationships,
  useRelationshipsByStatus,
} from "../api/relationships";
import { useSourceSchema } from "../api/snapshots";
import type { SourceSystem } from "../api/systems";
import type { Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { ErCanvas, matchesSearch, type ErCanvasHandle } from "./ErCanvas";
import { ErTableDetails } from "./ErTableDetails";
import { RelationshipsTable } from "./RelationshipsTable";
import { filterGraph, qualified, relationshipEdges } from "./erGraph";
import {
  EmptyState,
  ErrorText,
  Hint,
  NativeSelect,
  Panel,
} from "./parts";

const toolbarLabelClasses = "text-xs text-subtle-foreground";

/** Two or more buttons of which one is on (Keys only / All columns, All lines / On hover). */
function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; title?: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="inline-flex overflow-hidden rounded-md border border-border-strong"
    >
      {options.map((option, i) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          title={option.title}
          className={cn(
            "h-8 cursor-pointer bg-surface-raised px-2.5 text-sm text-muted-foreground hover:bg-muted aria-pressed:bg-primary-subtle aria-pressed:font-medium aria-pressed:text-primary",
            i > 0 && "border-l border-border-strong",
          )}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The way to the review: how many inferred relationships wait for a decision, and a link to
 * the Relationships tab of this system's Source schema (the ER diagram only reads).
 */
function ReviewLink({ systemId, pending }: { systemId: string; pending: number | undefined }) {
  const router = useRouter();
  const pathname = usePathname();
  const href = `${pathname}?${new URLSearchParams({
    folder: `systems/${systemId}/source-schema`,
    tab: "relationships",
  })}`;
  return (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        router.push(href);
      }}
      className="group inline-flex flex-wrap items-center gap-x-2 rounded-md text-sm text-muted-foreground"
    >
      {pending ? (
        <span>
          <span className="font-medium text-foreground tabular-nums">{pending}</span>{" "}
          suggested {pending === 1 ? "relationship" : "relationships"} to review
        </span>
      ) : (
        <span>No relationships waiting for review</span>
      )}
      <span aria-hidden className="text-subtle-foreground">
        ·
      </span>
      <span className="inline-flex items-center gap-1 font-medium text-primary group-hover:underline">
        {pending ? "Review in Source schema" : "Source schema"}
        <ArrowRight aria-hidden className="size-3.5" />
      </span>
    </a>
  );
}

/**
 * A Source System's ER diagram (spec story 60): its tables and views with the foreign keys
 * the latest Snapshot declares and the inferred relationships a member accepted. Narrow it
 * by Database Schema, or focus on one table and its neighbours; search dims the tables that
 * don't match. Any member can view it; inferred relationships are found and reviewed in
 * the Source schema, which the header links to.
 */
export function SourceErDiagram({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const schema = useSourceSchema(workspace.id, system.id, true);
  const accepted = useAcceptedRelationships(workspace.id, system.id);
  const suggested = useRelationshipsByStatus(workspace.id, system.id, "suggested");
  const [dbSchema, setDbSchema] = useState("");
  const [tableId, setTableId] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"keys" | "all">("keys");
  const [lines, setLines] = useState<"all" | "hover">("all");
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const canvas = useRef<ErCanvasHandle>(null);
  const search = useRef<HTMLInputElement>(null);

  const graph = useMemo(() => {
    if (!schema.data || !accepted.data) {
      return null;
    }
    const all = relationshipEdges(schema.data.tables, accepted.data);
    const present = schema.data.tables.filter(
      (t) => !t.status || t.status === "present",
    );
    return {
      all,
      present,
      byId: new Map(present.map((t) => [t.id, t])),
      shown: filterGraph(schema.data.tables, all, { dbSchema, tableId }),
    };
  }, [schema.data, accepted.data, dbSchema, tableId]);

  // "/" focuses the search, unless the user is typing or a dialog is open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.defaultPrevented) return;
      const target = event.target as Element | null;
      if (target?.closest?.("input, select, textarea, [contenteditable='true'], [role='dialog']")) {
        return;
      }
      if (!search.current) return;
      event.preventDefault();
      search.current.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (schema.isPending || accepted.isPending) {
    return <Loading label="Loading the ER diagram…" className="p-0" />;
  }
  if (schema.isError || accepted.isError) {
    const message = (schema.error ?? accepted.error)?.message;
    return <ErrorText>Could not load the ER diagram: {message}</ErrorText>;
  }
  if (!graph) {
    return null;
  }
  const { tables, edges } = graph.shown;
  const shownIds = new Set(tables.map((t) => t.id));
  const reveal = (id: string) => {
    if (shownIds.has(id)) canvas.current?.reveal(id);
  };
  const openTable = (id: string) => {
    reveal(id);
    setDetailsId(id);
  };
  const focused = tableId ? graph.byId.get(tableId) : undefined;

  /** Enter centres the first match: an exact table name, then a partial one, then a column. */
  const jumpToMatch = () => {
    const q = query.trim().toLowerCase();
    if (q === "") return;
    const hit =
      tables.find((t) => t.name.toLowerCase() === q) ??
      tables.find((t) => t.name.toLowerCase().includes(q)) ??
      tables.find((t) => matchesSearch(t, q));
    if (hit) reveal(hit.id);
  };

  return (
    <Panel aria-labelledby="folder-title">
      <div className="flex flex-wrap items-start justify-end gap-3">
        <h3 id="folder-title" className="sr-only">
          ER diagram
        </h3>
        <ReviewLink systemId={system.id} pending={suggested.data?.length} />
      </div>
      <div className="overflow-hidden rounded-pill border border-border bg-surface">
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-surface-raised px-2.5 py-2">
          <div className="relative min-w-0 flex-auto sm:max-w-xs">
            <Search
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-subtle-foreground"
            />
            <Input
              ref={search}
              type="search"
              aria-label="Search tables or columns"
              placeholder="Search tables or columns"
              autoComplete="off"
              className="h-8 ps-8 pe-8 text-sm"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  jumpToMatch();
                } else if (event.key === "Escape") {
                  setQuery("");
                }
              }}
            />
            <kbd
              aria-hidden
              className="pointer-events-none absolute top-1/2 right-2 hidden -translate-y-1/2 rounded-sm border border-border bg-surface px-1 font-mono text-2xs text-subtle-foreground sm:block"
            >
              /
            </kbd>
          </div>
          <span className="inline-flex items-center gap-1.5">
            <label htmlFor="er-schema" className={toolbarLabelClasses}>
              Database Schema
            </label>
            <NativeSelect
              id="er-schema"
              className="h-8 w-auto text-sm"
              value={dbSchema}
              onChange={(event) => {
                setDbSchema(event.target.value);
                setTableId("");
              }}
            >
              <option value="">All Database Schemas</option>
              {schema.data.db_schemas.map((s) => (
                <option key={s.id} value={s.name}>
                  {s.name}
                </option>
              ))}
            </NativeSelect>
          </span>
          <span className="inline-flex min-w-0 items-center gap-1.5">
            <label htmlFor="er-table" className={toolbarLabelClasses}>
              Focus on a table
            </label>
            <NativeSelect
              id="er-table"
              className="h-8 w-auto min-w-0 font-mono text-mono"
              value={tableId}
              onChange={(event) => setTableId(event.target.value)}
            >
              <option value="">All tables</option>
              {graph.present
                .filter((t) => dbSchema === "" || t.db_schema === dbSchema)
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {qualified(t)}
                  </option>
                ))}
            </NativeSelect>
          </span>
          <span className="flex-1" />
          <Segmented
            label="Columns shown"
            value={mode}
            onChange={setMode}
            options={[
              { value: "keys", label: "Keys only" },
              { value: "all", label: "All columns" },
            ]}
          />
          <Segmented
            label="Relationship lines"
            value={lines}
            onChange={setLines}
            options={[
              { value: "all", label: "All lines", title: "Draw every relationship line" },
              { value: "hover", label: "On hover", title: "Draw only the hovered table's lines" },
            ]}
          />
        </div>
        {tables.length === 0 ? (
          <EmptyState className="m-4">No tables match these filters.</EmptyState>
        ) : (
          <ErCanvas
            ref={canvas}
            tables={tables}
            edges={edges}
            allEdges={graph.all}
            mode={mode}
            lines={lines}
            query={query}
            selectedId={detailsId}
            onOpen={setDetailsId}
            focus={
              focused && { name: qualified(focused), onClear: () => setTableId("") }
            }
          />
        )}
      </div>

      <section aria-labelledby="er-relationships" className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h4 id="er-relationships" className="flex items-center gap-2 text-lg font-semibold">
            Relationships
            <Badge className="tabular-nums">{graph.all.length}</Badge>
          </h4>
          <Hint>
            Declared foreign keys from the Snapshot, plus inferred relationships accepted in
            the Source schema.
          </Hint>
        </div>
        <RelationshipsTable edges={graph.all} tables={graph.byId} onOpenTable={openTable} />
      </section>

      <ErTableDetails
        table={detailsId ? (graph.byId.get(detailsId) ?? null) : null}
        tables={graph.byId}
        edges={graph.all}
        onClose={() => setDetailsId(null)}
        onOpenTable={openTable}
      />
    </Panel>
  );
}
