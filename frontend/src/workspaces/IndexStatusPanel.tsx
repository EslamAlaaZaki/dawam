"use client";

import {
  useIndexStatus,
  useReindexAllDocuments,
  useReindexDocument,
  type FileIndexStatus,
} from "../api/files";
import { allows, type Workspace } from "../api/workspaces";
import { PageHeader } from "../components/PageHeader";
import { WarningMark } from "../components/WarningMark";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import { RetrievalPlayground } from "./RetrievalPlayground";
import { EmptyState, ErrorText, Hint, TableCard, monoClasses } from "./parts";

const STATUS: Record<string, string> = {
  queued: "Queued",
  extracting: "Extracting",
  ocr: "Reading scans",
  chunking: "Chunking",
  embedding: "Embedding",
  indexed: "Indexed",
  failed: "Failed",
};

/** The index badge; like the Documents folder, an indexed file whose passages were not
 * (all) embedded shows a "!" with the reason, and reads Text only with no vector at all. */
function StatusCell({ row }: { row: FileIndexStatus }) {
  const indexed = row.index_status === "indexed";
  const textOnly = indexed && row.text_only;
  const variant = textOnly
    ? "warning"
    : row.index_status === "failed"
      ? "danger"
      : indexed
        ? "success"
        : "neutral";
  return (
    <div className="flex flex-col items-start gap-1">
      <span className="inline-flex items-center">
        <Badge variant={variant}>
          {textOnly
            ? "Text only"
            : (STATUS[row.index_status] ?? row.index_status)}
        </Badge>
        {indexed && row.embedding_error && (
          <WarningMark message={row.embedding_error} />
        )}
      </span>
      {row.index_error && (
        <span className="text-xs text-muted-foreground">{row.index_error}</span>
      )}
    </div>
  );
}

function source(row: FileIndexStatus): string {
  return row.source_kind === "data_warehouse"
    ? "Data Warehouse"
    : (row.source_name ?? "—");
}

function coverage(row: FileIndexStatus): string {
  if (row.chunk_count === 0) {
    return "—";
  }
  const percent = Math.round((row.embedded_count * 100) / row.chunk_count);
  return `${row.embedded_count} of ${row.chunk_count} (${percent}%)`;
}

/**
 * The index status page: every file of the Workspace with its source (a Source System or
 * the Data Warehouse), where its indexing is, its
 * passages, how many of them are embedded, how many were embedded by an older model and
 * the last error. Owners and editors index one file or all of them again.
 */
export function IndexStatusPanel({ workspace }: { workspace: Workspace }) {
  const status = useIndexStatus(workspace.id);
  const reindexOne = useReindexDocument(workspace.id);
  const reindexAll = useReindexAllDocuments(workspace.id);
  const canManage = allows(workspace, "file.upload");
  const rows = status.data ?? [];

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-5">
      <PageHeader
        title={<span id="folder-title">Document index</span>}
        actions={
          canManage && rows.length > 0 ? (
            <Button
              type="button"
              disabled={reindexAll.isPending}
              onClick={() =>
                reindexAll.mutate(undefined, {
                  onSuccess: () => toast.success("Re-indexing all documents."),
                })
              }
            >
              Re-index all
            </Button>
          ) : undefined
        }
      />
      {reindexAll.isError && <ErrorText>{reindexAll.error.message}</ErrorText>}
      {reindexOne.isError && <ErrorText>{reindexOne.error.message}</ErrorText>}
      {status.isPending ? (
        <Loading className="p-0" />
      ) : status.isError ? (
        <ErrorText>Could not load the index: {status.error.message}</ErrorText>
      ) : rows.length === 0 ? (
        <EmptyState>
          No documents yet. Uploaded files are indexed here.
        </EmptyState>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">File</TableHead>
                <TableHead scope="col">Source</TableHead>
                <TableHead scope="col">Status</TableHead>
                <TableHead scope="col">Passages</TableHead>
                <TableHead scope="col">Embedded</TableHead>
                <TableHead scope="col">Stale vectors</TableHead>
                {canManage && <TableHead scope="col">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.file_id}>
                  <TableCell className={monoClasses}>{row.name}</TableCell>
                  <TableCell>{source(row)}</TableCell>
                  <TableCell>
                    <StatusCell row={row} />
                  </TableCell>
                  <TableCell>{row.chunk_count}</TableCell>
                  <TableCell>{coverage(row)}</TableCell>
                  <TableCell>
                    {row.stale_count > 0 ? (
                      <Badge variant="warning">{row.stale_count} stale</Badge>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  {canManage && (
                    <TableCell>
                      <Button
                        type="button"
                        size="sm"
                        disabled={reindexOne.isPending}
                        onClick={() => reindexOne.mutate(row.file_id)}
                      >
                        Re-index {row.name}
                      </Button>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {!canManage && (
        <Hint>Only owners and editors can re-index documents.</Hint>
      )}
      <RetrievalPlayground workspaceId={workspace.id} />
    </section>
  );
}
