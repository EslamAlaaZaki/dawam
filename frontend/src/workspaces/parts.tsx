import type { ComponentProps, ReactNode } from "react";

import { cn } from "../lib/utils";

export {
  EmptyState,
  formCardClasses,
  h3Classes,
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
} from "../admin/parts";

/** The prototype's mono face, for identifiers (System Codes, table and column names, SQL). */
export const monoClasses = "font-mono text-mono";

/** Headings inside a panel; the reset leaves them unstyled. */
export const h4Classes = "text-base font-semibold";
export const h5Classes = "text-sm font-semibold text-muted-foreground";

/** A panel's section: a card around its content. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-pill border border-border bg-surface-raised p-5",
        className,
      )}
      {...props}
    />
  );
}

/** A secondary line: what a control does, or what a number means. */
export function Hint({ className, ...props }: ComponentProps<"p">) {
  return (
    <p className={cn("text-sm text-muted-foreground", className)} {...props} />
  );
}

/** A success note, shown inline where the user acted. */
export function OkText({ className, ...props }: ComponentProps<"p">) {
  return (
    <p className={cn("text-sm text-success-strong", className)} {...props} />
  );
}

/** Term and value pairs; use plain `dt` and `dd` inside. */
export function DetailList({ className, ...props }: ComponentProps<"dl">) {
  return (
    <dl
      className={cn(
        "grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1.5 [&_dd]:min-w-0 [&_dd]:break-words [&_dt]:font-medium [&_dt]:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

/** The row of a form's buttons. */
export function Actions({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {children}
    </div>
  );
}

/** A download or other plain link. */
export const linkClasses =
  "font-medium text-primary underline underline-offset-2 hover:text-primary-hover";

/** A button that reads as a mono identifier link (a table or routine to open). */
export const linkButtonClasses =
  "cursor-pointer rounded-sm font-mono text-mono text-primary underline-offset-2 hover:underline";

/** Code and SQL. */
export const preClasses =
  "max-h-80 overflow-auto rounded-lg border border-border bg-surface p-3 font-mono text-mono";

/** A bordered list of rows, for object lists. */
export const listCardClasses =
  "divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-raised";

/** A native checkbox in the accent colour; its label wraps it in a row. */
export const checkClasses = "size-4 accent-primary";
export const checkLabelClasses = "flex items-center gap-2 text-sm font-medium";

/** The fieldset of a form: no border, controls stacked like the card around it. */
export const fieldsetClasses = "m-0 flex min-w-0 flex-col gap-3.5 border-0 p-0";
