"use client";

import { ArrowRight, ChevronDown, Download, Search } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { cn } from "../lib/utils";

const LAYER_STEPS = ["Source", "Staging", "Core", "Mart"] as const;

const layerStep = (active: boolean) =>
  cn(
    "rounded-md px-2 py-0.5 font-medium",
    active
      ? "bg-primary-subtle text-primary"
      : "bg-muted text-muted-foreground",
  );

/** The Layer strip of a Mappings page: the Layers a Layer is built between are highlighted. */
export function LayerStrip({
  active,
}: {
  active: (typeof LAYER_STEPS)[number][];
}) {
  return (
    <ol
      aria-label="Layers"
      className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground"
    >
      {LAYER_STEPS.map((step, i) => {
        const on = active.includes(step);
        const next = LAYER_STEPS[i + 1];
        return (
          <li key={step} className="contents">
            <span className={layerStep(on)}>{step}</span>
            {next && (
              <ArrowRight
                aria-hidden
                className={cn(
                  "size-3",
                  on && active.includes(next) && "text-primary",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** The "Export mapping sheet" menu: the sheet as XLSX or CSV (any member). */
export function ExportSheetMenu({
  url,
}: {
  url: (format: "xlsx" | "csv") => string;
}) {
  const item = (format: "xlsx" | "csv", label: ReactNode) => (
    <DropdownMenuItem asChild>
      <a href={url(format)} download>
        <Download aria-hidden />
        {label}
      </a>
    </DropdownMenuItem>
  );
  return (
    <div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" variant="secondary">
            <Download aria-hidden />
            Export mapping sheet
            <ChevronDown aria-hidden className="text-subtle-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="min-w-48">
          {item("xlsx", "Download XLSX")}
          {item("csv", "Download CSV")}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/** A state as a coloured dot, named for assistive tech and the tooltip. */
export function StateDot({
  label,
  className,
}: {
  label: string;
  className: string;
}) {
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn("size-1.5 shrink-0 rounded-full", className)}
    />
  );
}

/**
 * The explorer card of a Mappings page, like the Source schema page's: a searchable tree of
 * tables on the left with a legend under it, and the chosen table's detail on the right.
 */
export function MappingsExplorer({
  idPrefix,
  placeholder,
  treeLabel,
  search,
  onSearch,
  empty,
  tree,
  legend,
  children,
}: {
  idPrefix: string;
  placeholder: string;
  treeLabel: string;
  search: string;
  onSearch: (value: string) => void;
  /** True when nothing matches: the tree gives way to a line saying so. */
  empty: boolean;
  tree: ReactNode;
  legend: { label: string; className: string }[];
  children: ReactNode;
}) {
  return (
    <div className="grid overflow-hidden rounded-pill border border-border lg:grid-cols-[minmax(0,19rem)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col border-b border-border bg-surface lg:border-e lg:border-b-0">
        <div className="flex flex-col gap-3 border-b border-border p-3">
          <label htmlFor={`${idPrefix}-search`} className="sr-only">
            Search by name
          </label>
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute start-2.5 top-2 size-4 text-subtle-foreground"
            />
            <Input
              id={`${idPrefix}-search`}
              type="search"
              value={search}
              placeholder={placeholder}
              onChange={(event) => onSearch(event.target.value)}
              className="ps-8"
            />
          </div>
        </div>
        {empty ? (
          <p className="flex-1 p-4 text-sm text-muted-foreground">
            No table matches.
          </p>
        ) : (
          <ul
            aria-label={treeLabel}
            className="flex flex-1 flex-col gap-px overflow-auto p-2"
          >
            {tree}
          </ul>
        )}
        <ul
          aria-label="Legend"
          className="flex flex-wrap gap-x-3 gap-y-1 border-t border-border px-4 pt-2 pb-3 text-xs text-subtle-foreground"
        >
          {legend.map(({ label, className }) => (
            <li key={label} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className={cn("size-1.5 rounded-full", className)}
              />
              {label}
            </li>
          ))}
        </ul>
      </div>
      <div className="flex min-w-0 flex-col bg-surface-raised">{children}</div>
    </div>
  );
}
