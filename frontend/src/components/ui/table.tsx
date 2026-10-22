import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/** The shared table: sticky header, row hover, compact rows, tabular numbers. */
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
        "sticky top-0 border-b border-border bg-surface px-3 py-2 text-left text-xs font-medium whitespace-nowrap text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "border-b border-border px-3 py-2.5 align-middle whitespace-nowrap group-hover/row:bg-muted",
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
