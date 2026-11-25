"use client";

import { ArrowRight, ChevronDown, Download } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../components/ui/button";
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
    active ? "bg-primary-subtle text-primary" : "bg-muted text-muted-foreground",
  );

/** The Layer strip of a Mappings page: the Layers a Layer is built between are highlighted. */
export function LayerStrip({ active }: { active: (typeof LAYER_STEPS)[number][] }) {
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
                className={cn("size-3", on && active.includes(next) && "text-primary")}
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
