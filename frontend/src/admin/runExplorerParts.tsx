"use client";

import { X } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Button } from "../components/ui/button";
import { SheetClose, SheetDescription, SheetTitle } from "../components/ui/sheet";

/** Pieces the "Assistant runs" and "Jobs & search" tabs of the AI runs page share. */

export function statusVariant(status: string): "success" | "danger" | "warning" | "neutral" {
  if (status === "completed" || status === "ok") return "success";
  if (status === "failed") return "danger";
  if (status === "tool_limit" || status === "cancelled" || status === "partial") return "warning";
  return "neutral";
}

/** Midnight at the start of a `YYYY-MM-DD` day in the admin's time zone, `days` later. */
export function startOfDay(day: string, days = 0): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year!, month! - 1, date! + days).toISOString();
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

export type Money = { amount: string; currency: string } | null;

export function money(cost: Money): string {
  return cost ? `${Number(cost.amount).toFixed(6)} ${cost.currency}` : "unpriced";
}

/** A duration as "420 ms", "8.4 s" or "1m 5s". */
export function duration(ms: number): string {
  if (ms >= 60000) return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** A token count, shortened from a thousand up ("24.2k"). */
export function count(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  return n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
}

export function Cost({ cost }: { cost: Money }) {
  return cost ? (
    <>{money(cost)}</>
  ) : (
    <span className="text-subtle-foreground">unpriced</span>
  );
}

export interface Stat {
  label: string;
  value: ReactNode;
  hint: string;
}

/** The summary row: one cell per stat. */
export function SummaryStats({ stats }: { stats: Stat[] }) {
  return (
    <dl
      aria-label="Summary"
      className="grid grid-cols-2 overflow-hidden rounded-pill border border-border bg-surface-raised sm:grid-cols-5"
    >
      {stats.map((stat) => (
        <div
          key={stat.label}
          className="flex min-w-0 flex-col gap-0.5 border-s border-b border-border px-3.5 py-3 first:border-s-0 max-sm:odd:border-s-0 sm:border-b-0"
        >
          <dt className="text-xs text-subtle-foreground">{stat.label}</dt>
          <dd className="truncate text-2xl font-semibold tabular-nums">{stat.value}</dd>
          <dd className="text-xs text-subtle-foreground">{stat.hint}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The row-opens-a-side-sheet behaviour: which row is open, the last one opened (so the sheet
 * stays filled while it closes), and focus returning to the row when the sheet closes.
 */
export function useRowSheet() {
  const [selected, setSelected] = useState<string | null>(null);
  const [last, setLast] = useState<string | null>(null);
  const opener = useRef<string | null>(null);
  const rows = useRef(new Map<string, HTMLTableRowElement>());

  function open(id: string) {
    opener.current = id;
    setSelected(id);
    setLast(id);
  }
  return {
    selected,
    last,
    open,
    close: () => setSelected(null),
    onRowKey(event: KeyboardEvent<HTMLTableRowElement>, id: string) {
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open(id);
      }
    },
    rowRef: (id: string) => (node: HTMLTableRowElement | null) => {
      if (node) rows.current.set(id, node);
      else rows.current.delete(id);
    },
    onCloseAutoFocus(event: Event) {
      const row = opener.current ? rows.current.get(opener.current) : undefined;
      if (row) {
        event.preventDefault();
        row.focus();
      }
    },
  };
}

export const rowClasses =
  "cursor-pointer outline-none focus-visible:bg-muted focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-inset data-[state=selected]:bg-primary-subtle";

/** The sheet's header: the title with a badge, the id under it, and a close button. */
export function SheetHeader({
  title,
  badge,
  id,
}: {
  title: ReactNode;
  badge: ReactNode;
  id: string;
}) {
  return (
    <header className="flex items-start gap-3 border-b border-border px-5 py-4">
      <div className="min-w-0 flex-1">
        <SheetTitle className="flex items-center gap-2 text-lg font-semibold">
          {title}
          {badge}
        </SheetTitle>
        <SheetDescription className="mt-0.5 font-mono text-xs break-all text-subtle-foreground">
          {id}
        </SheetDescription>
      </div>
      <SheetClose asChild>
        <Button type="button" variant="ghost" size="icon" aria-label="Close">
          <X aria-hidden />
        </Button>
      </SheetClose>
    </header>
  );
}

/** A label-over-value grid. */
export function Details({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-5 gap-y-3 text-sm sm:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-subtle-foreground">{label}</dt>
          <dd className="break-words tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
