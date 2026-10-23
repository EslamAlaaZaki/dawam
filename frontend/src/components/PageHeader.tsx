import type { ReactNode } from "react";

/** A page's title, its one-line description and a slot for the page's existing actions. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
      <div className="min-w-0">
        <h2 className="text-2xl font-semibold tracking-tight text-balance text-foreground">
          {title}
        </h2>
        {description && (
          <p className="mt-0.5 text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && (
        <div className="ms-auto flex flex-wrap items-center gap-2">
          {actions}
        </div>
      )}
    </div>
  );
}
