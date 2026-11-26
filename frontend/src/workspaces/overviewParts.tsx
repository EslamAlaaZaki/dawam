"use client";

import { ArrowRight, Database } from "lucide-react";
import type { ReactNode } from "react";

/**
 * A card on a Layer's overview page that opens one of the Layer's folders. `folder` is the
 * full folder path, e.g. `dw/core/model`.
 */
export function Card({
  name,
  label,
  folder,
  onOpen,
  children,
}: {
  name: string;
  label: string;
  folder: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <a
      href={`?${new URLSearchParams({ folder })}`}
      aria-label={`${label}. Open ${name}`}
      onClick={(event) => {
        event.preventDefault();
        onOpen();
      }}
      className="group flex min-w-0 flex-col gap-3 rounded-pill border border-border bg-surface-raised p-4 text-foreground transition-colors duration-120 hover:border-border-strong hover:bg-surface"
    >
      <span className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-muted-foreground [&_svg]:size-4 [&_svg]:text-subtle-foreground">
          <Database aria-hidden />
          {name}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-medium whitespace-nowrap text-subtle-foreground transition-colors duration-120 group-hover:text-primary [&_svg]:size-3.5">
          Open
          <ArrowRight aria-hidden />
        </span>
      </span>
      {children}
    </a>
  );
}

export function Big({
  value,
  unit,
}: {
  value: number | string;
  unit: string;
}) {
  return (
    <span className="flex items-baseline gap-2 text-3xl font-semibold tabular-nums">
      {value}
      <small className="text-sm font-normal text-muted-foreground">{unit}</small>
    </span>
  );
}
