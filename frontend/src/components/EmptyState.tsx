import type { ComponentProps, ReactNode } from "react";

import { cn } from "../lib/utils";

/** What a list shows when it has nothing yet: an optional icon and title, then its one sentence. */
export function EmptyState({
  icon,
  title,
  className,
  children,
  ...props
}: Omit<ComponentProps<"div">, "title"> & {
  icon?: ReactNode;
  title?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 rounded-pill border border-dashed border-border-strong px-6 py-8 text-center",
        className,
      )}
      {...props}
    >
      {icon && (
        <span aria-hidden className="text-subtle-foreground [&_svg]:size-5">
          {icon}
        </span>
      )}
      {title && (
        <div className="text-md font-medium text-foreground">{title}</div>
      )}
      <p className="max-w-xl text-sm text-muted-foreground">{children}</p>
    </div>
  );
}
