import type { ComponentProps, ReactNode } from "react";

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
        <h2 className="text-2xl font-semibold tracking-[-0.01em] text-balance">{title}</h2>
        <p className="mt-0.5 text-sand-11">{description}</p>
      </div>
      {actions && <div className="ml-auto flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** A native select in the text-field look (the pages' forms and tests drive it as a plain select). */
export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(fieldClasses, "h-[34px]", className)} {...props} />;
}

/** The label above a form control. */
export const labelClasses = "flex flex-col gap-1.5 text-sm font-medium";

/** The card around a table (prototype "card-table"). */
export function TableCard({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-sand-5 bg-sand-1">
      {children}
      {footer && (
        <div className="flex flex-wrap justify-between gap-2 border-t border-sand-4 px-3 py-2 text-xs text-sand-11">
          {footer}
        </div>
      )}
    </div>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-red-11" role="alert">
      {children}
    </p>
  );
}
