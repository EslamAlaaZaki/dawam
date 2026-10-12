import { Loader2 } from "lucide-react";

/** What a page shows while it waits for the API. */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="dawam-ui flex items-center gap-2 p-6 text-sm text-current opacity-70">
      <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
      {label}
    </p>
  );
}
