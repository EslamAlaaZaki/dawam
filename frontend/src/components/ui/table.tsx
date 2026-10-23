import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/**
 * The shared table: sticky header, 40px rows, row hover, tabular numbers. Pass `mono` to a
 * cell for identifiers and `numeric` to right-align numbers.
 */
function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table data-slot="table" className={cn("w-full border-collapse tabular-nums", className)} {...props} />
    </div>
  );
}

function TableHeader(props: ComponentProps<"thead">) {
  return <thead data-slot="table-header" {...props} />;
}

function TableBody({ className, ...props }: ComponentProps<"tbody">) {
  return (
    <tbody data-slot="table-body" className={cn("[&_tr:last-child>td]:border-b-0", className)} {...props} />
  );
}

function TableFooter({ className, ...props }: ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn("border-t border-border text-mono text-muted-foreground", className)}
      {...props}
    />
  );
}

function TableRow({ className, ...props }: ComponentProps<"tr">) {
  return <tr data-slot="table-row" className={cn("group/row", className)} {...props} />;
}

function TableHead({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "sticky top-0 h-10 border-b border-border bg-surface px-3 text-left text-xs font-medium whitespace-nowrap text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

function TableCell({
  className,
  mono = false,
  numeric = false,
  ...props
}: ComponentProps<"td"> & { mono?: boolean; numeric?: boolean }) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        mono && "font-mono",
        numeric && "text-right",
        "h-10 border-b border-border px-3 py-1 align-middle whitespace-nowrap group-hover/row:bg-muted",
        className,
      )}
      {...props}
    />
  );
}

function TableCaption({ className, ...props }: ComponentProps<"caption">) {
  return <caption data-slot="table-caption" className={cn("mt-2 text-sm text-muted-foreground", className)} {...props} />;
}

export { Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow };
