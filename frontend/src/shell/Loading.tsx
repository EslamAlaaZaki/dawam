import { Loader2 } from "lucide-react";

import { cn } from "../lib/utils";

/**
 * What a page or panel shows while it waits for the API. A page gets room around it;
 * a panel passes `className="p-0"` to sit flush in its card.
 */
export function Loading({
  label = "Loading…",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "dawam-ui flex items-center gap-2 text-sm text-muted-foreground",
        className ?? "p-6",
      )}
    >
      <Loader2
        className="size-4 text-subtle-foreground motion-safe:animate-spin"
        aria-hidden
      />
      {label}
    </p>
  );
}
