import type { ComponentProps, ReactNode } from "react";

import { cn } from "../lib/utils";

export {
  ErrorText,
  NativeSelect,
  TableCard,
  labelClasses,
} from "../admin/parts";

/** The prototype's mono face, for identifiers (System Codes, table and column names, SQL). */
export const monoClasses = "font-mono text-mono";

/** Headings inside a panel; the reset leaves them unstyled. */
export const h3Classes = "text-md font-semibold tracking-[-0.005em]";
export const h4Classes = "text-base font-semibold";
export const h5Classes = "text-sm font-semibold text-sand-11";

/** A card of fields: the panel's form, one control per row. */
export const formCardClasses =
  "flex max-w-xl flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5";

/** A panel's section: a card around its content. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-xl border border-sand-5 bg-sand-1 p-5",
        className,
      )}
      {...props}
    />
  );
}

/** A secondary line: what a control does, or what a number means. */
export function Hint({ className, ...props }: ComponentProps<"p">) {
  return <p className={cn("text-sm text-sand-11", className)} {...props} />;
}

/** A success note, shown inline where the user acted. */
export function OkText({ className, ...props }: ComponentProps<"p">) {
  return <p className={cn("text-sm text-grass-11", className)} {...props} />;
}

/** What a list shows when it has nothing yet. */
export function EmptyState({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      className={cn(
        "max-w-xl rounded-lg border border-dashed border-sand-8 px-4 py-3 text-sand-11",
        className,
      )}
      {...props}
    />
  );
}

/** Term and value pairs; use plain `dt` and `dd` inside. */
export function DetailList({ className, ...props }: ComponentProps<"dl">) {
  return (
    <dl
      className={cn(
        "grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1.5 [&_dd]:min-w-0 [&_dd]:break-words [&_dt]:font-medium [&_dt]:text-sand-11",
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
  "font-medium text-jade-11 underline underline-offset-2 hover:text-sand-12";

/** A button that reads as a mono identifier link (a table or routine to open). */
export const linkButtonClasses =
  "cursor-pointer rounded-sm font-mono text-mono text-jade-11 underline-offset-2 hover:underline";

/** Code and SQL. */
export const preClasses =
  "max-h-80 overflow-auto rounded-lg border border-sand-5 bg-sand-2 p-3 font-mono text-mono";

/** A bordered list of rows, for object lists. */
export const listCardClasses =
  "divide-y divide-sand-4 overflow-hidden rounded-lg border border-sand-5 bg-sand-1";

/** A native checkbox in the accent colour; its label wraps it in a row. */
export const checkClasses = "size-4 accent-[var(--accent-solid)]";
export const checkLabelClasses = "flex items-center gap-2 text-sm font-medium";

/** The fieldset of a form: no border, controls stacked like the card around it. */
export const fieldsetClasses = "m-0 flex min-w-0 flex-col gap-3.5 border-0 p-0";
