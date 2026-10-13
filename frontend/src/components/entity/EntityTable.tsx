import type { ReactNode } from "react";

import { EmptyState, ErrorText, TableCard } from "../../admin/parts";
import { Loading } from "../../shell/Loading";
import { Button } from "../ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";

export interface EntityColumn<T> {
  header: string;
  cell: (row: T) => ReactNode;
  className?: string;
}

/**
 * The one table every entity list uses: columns, an Actions column per row, an empty
 * state that offers the create action, and Load more where the API pages.
 */
export function EntityTable<T>({
  rows,
  rowKey,
  rowLabel,
  columns,
  actions,
  isPending,
  error,
  loadErrorPrefix,
  emptyMessage,
  emptyAction,
  count,
  hasMore,
  isLoadingMore,
  onLoadMore,
}: {
  rows: T[] | undefined;
  rowKey: (row: T) => string;
  /** The row's accessible name. */
  rowLabel: (row: T) => string;
  columns: EntityColumn<T>[];
  actions?: (row: T) => ReactNode;
  isPending?: boolean;
  error?: Error | null;
  loadErrorPrefix?: string;
  emptyMessage: ReactNode;
  emptyAction?: ReactNode;
  /** What the footer counts, e.g. "users". */
  count?: string;
  hasMore?: boolean;
  isLoadingMore?: boolean;
  onLoadMore?: () => void;
}) {
  if (isPending) return <Loading />;
  if (error) {
    return (
      <ErrorText>
        {loadErrorPrefix ?? "Could not load the list:"} {error.message}
      </ErrorText>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <div className="flex flex-col items-start gap-3">
        <EmptyState>{emptyMessage}</EmptyState>
        {emptyAction}
      </div>
    );
  }
  return (
    <>
      <TableCard
        footer={
          count && (
            <span>
              {rows.length} {count}
              {hasMore && " so far"}
            </span>
          )
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column.header} scope="col">
                  {column.header}
                </TableHead>
              ))}
              {actions && <TableHead scope="col">Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={rowKey(row)} aria-label={rowLabel(row)}>
                {columns.map((column) => (
                  <TableCell key={column.header} className={column.className}>
                    {column.cell(row)}
                  </TableCell>
                ))}
                {actions && (
                  <TableCell className="whitespace-normal">{actions(row)}</TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableCard>
      {hasMore && (
        <Button className="self-start" onClick={onLoadMore} disabled={isLoadingMore}>
          Load more
        </Button>
      )}
    </>
  );
}
