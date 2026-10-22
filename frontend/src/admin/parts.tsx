import type { ComponentProps, ReactNode } from "react";

export { EmptyState } from "../components/EmptyState";
import { fieldClasses } from "../components/ui/input";
import { cn } from "../lib/utils";

/** The page title with its one-line description and the page's main actions. */
export function PageHead({
  title,
  description,
  actions,
}: {
  title: string;
  description: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight text-balance">
          {title}
        </h2>
        <p className="mt-0.5 text-muted-foreground">{description}</p>
      </div>
      {actions && <div className="ms-auto flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** A native select in the text-field look (the pages' forms and tests drive it as a plain select). */
export function NativeSelect({
  className,
  ...props
}: ComponentProps<"select">) {
  return (
    <select className={cn(fieldClasses, "h-[34px]", className)} {...props} />
  );
}

/** The label above a form control. */
export const labelClasses = "flex flex-col gap-1.5 text-sm font-medium";

/** The card around a table (prototype "card-table"). */
export function TableCard({
  children,
  footer,
}: {
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-pill border border-border bg-surface-raised">
      {children}
      {footer && (
        <div className="flex flex-wrap justify-between gap-2 border-t border-border px-3 py-2 text-xs text-muted-foreground">
          {footer}
        </div>
      )}
    </div>
  );
}

/** A card of fields: the panel's form, one control per row. */
export const formCardClasses =
  "flex max-w-xl flex-col gap-3.5 rounded-pill border border-border bg-surface-raised p-5";

/** Headings inside a panel; the reset leaves them unstyled. */
export const h3Classes = "text-md font-semibold tracking-tight";

export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-danger-strong" role="alert">
      {children}
    </p>
  );
}
