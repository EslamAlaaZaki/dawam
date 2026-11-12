"use client";

import { MoreHorizontal, Upload } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

import {
  areaZipUrl,
  fileDownloadUrl,
  isEditable,
  useAreaFiles,
  useFileText,
  useReplaceFile,
  useSaveFileText,
  useUploadFile,
  type FileArea,
  type WorkspaceFile,
} from "../api/files";
import { useMembers } from "../api/members";
import { allows, type Workspace } from "../api/workspaces";
import { PageHeader } from "../components/PageHeader";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { toast } from "../components/ui/sonner";
import { Textarea } from "../components/ui/textarea";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";
import {
  EmptyState,
  ErrorText,
  Hint,
  TableCard,
  labelClasses,
  linkClasses,
  monoClasses,
} from "./parts";
import { Loading } from "../shell/Loading";

const TEXT_STATUS: Record<string, string> = {
  extracted: "Text extracted",
  no_text_found: "No text found",
  none: "—",
};

const INDEX_STATUS: Record<string, string> = {
  queued: "Queued",
  extracting: "Extracting",
  ocr: "Reading scans",
  chunking: "Chunking",
  embedding: "Embedding",
  indexed: "Indexed",
  failed: "Failed",
};

/** Short labels for the MIME types the upload accepts; anything else falls back to its subtype. */
const TYPE_LABEL: Record<string, string> = {
  "application/pdf": "PDF",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "DOCX",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
  "text/markdown": "MD",
  "application/sql": "SQL",
  "text/x-sql": "SQL",
  "application/x-yaml": "YAML",
  "application/yaml": "YAML",
  "text/yaml": "YAML",
  "text/csv": "CSV",
  "application/json": "JSON",
  "text/plain": "TXT",
  "image/png": "PNG",
  "image/jpeg": "JPG",
  "image/gif": "GIF",
  "image/webp": "WEBP",
};

function typeLabel(mime: string): string {
  const known = TYPE_LABEL[mime];
  if (known) {
    return known;
  }
  const subtype = mime.split("/")[1] ?? mime;
  return (subtype.split("+")[0] ?? subtype).replace(/^x-/, "").toUpperCase();
}

/** The merged Text + Indexing column: the index badge, then one caption line. */
function StatusCell({ file }: { file: WorkspaceFile }) {
  const label = INDEX_STATUS[file.index_status] ?? file.index_status;
  const textStatus = TEXT_STATUS[file.text_status] ?? file.text_status;
  const variant =
    file.index_status === "indexed"
      ? "success"
      : file.index_status === "failed"
        ? "danger"
        : "info";
  const failed = file.index_status === "failed" && file.index_error;
  const caption = failed || file.index_note || textStatus;
  return (
    <div className="flex flex-col items-start gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant={variant}>{label}</Badge>
        </TooltipTrigger>
        <TooltipContent>{textStatus}</TooltipContent>
      </Tooltip>
      <span
        className={`text-xs ${failed ? "text-danger-strong" : "text-muted-foreground"}`}
      >
        {caption}
      </span>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TextEditor({
  workspaceId,
  area,
  file,
  onClose,
}: {
  workspaceId: string;
  area: FileArea;
  file: WorkspaceFile;
  onClose: () => void;
}) {
  const text = useFileText(workspaceId, file.id);
  const save = useSaveFileText(workspaceId, area, file.id);
  const [draft, setDraft] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save.mutate(draft ?? text.data ?? "", { onSuccess: onClose });
  }

  return (
    <form
      className="flex flex-col gap-3.5"
      onSubmit={onSubmit}
      aria-label={`Edit ${file.name}`}
    >
      <DialogHeader>
        <DialogTitle>Edit {file.name}</DialogTitle>
        <DialogDescription className="sr-only">
          Edit the text of this file in the browser.
        </DialogDescription>
      </DialogHeader>
      {text.isPending ? (
        <Loading className="p-0" />
      ) : text.isError ? (
        <ErrorText>Could not load the file: {text.error.message}</ErrorText>
      ) : (
        <label className={labelClasses}>
          Content
          <Textarea
            rows={16}
            spellCheck={false}
            className={monoClasses}
            value={draft ?? text.data}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
      )}
      <Hint>Saving overwrites the file; there is no version history.</Hint>
      {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      <DialogFooter>
        <Button type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="primary"
          disabled={!text.isSuccess || save.isPending}
        >
          Save
        </Button>
      </DialogFooter>
    </form>
  );
}

/** The row's `⋯` menu: download, then edit (text files) or replace (the others). */
function RowActions({
  workspaceId,
  area,
  file,
  onEdit,
}: {
  workspaceId: string;
  area: FileArea;
  file: WorkspaceFile;
  onEdit: () => void;
}) {
  const replace = useReplaceFile(workspaceId, area, file.id);
  const picker = useRef<HTMLInputElement>(null);
  return (
    <div className="flex flex-col items-end gap-1">
      <input
        ref={picker}
        type="file"
        hidden
        aria-label={`Replace ${file.name}`}
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) {
            replace.mutate(chosen);
          }
          event.target.value = "";
        }}
      />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Actions for ${file.name}`}
          >
            <MoreHorizontal aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <a
              href={fileDownloadUrl(workspaceId, file.id)}
              download={file.name}
              aria-label={`Download ${file.name}`}
            >
              Download
            </a>
          </DropdownMenuItem>
          {isEditable(file) ? (
            <DropdownMenuItem
              aria-label={`Edit ${file.name}`}
              onSelect={onEdit}
            >
              Edit
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem
              aria-label={`Replace ${file.name}`}
              onSelect={() => picker.current?.click()}
            >
              Replace…
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      {replace.isError && <ErrorText>{replace.error.message}</ErrorText>}
    </div>
  );
}

/**
 * The file area of a Source System (its Documents folder) or of the Data Warehouse: every
 * file with its type, size, author and last update; any member downloads a file or the
 * whole area as a zip; owners and editors edit text files in the browser, replace
 * binary ones, and upload.
 */
export function DocumentsPanel({
  workspace,
  area,
  title,
  ownerName,
  hideTitle = false,
}: {
  workspace: Workspace;
  area: FileArea;
  title: string;
  ownerName: string;
  /** Keep the title for screen readers only: the breadcrumb already names the page. */
  hideTitle?: boolean;
}) {
  const files = useAreaFiles(workspace.id, area);
  const members = useMembers(workspace.id);
  const upload = useUploadFile(workspace.id, area);
  const [editing, setEditing] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const canEdit = allows(workspace, "file.upload");
  const authors = new Map(
    (members.data ?? []).map((m) => [m.user_id, m.display_name]),
  );
  const editedFile = files.data?.find((file) => file.id === editing);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("file") as HTMLInputElement;
    const file = input.files?.[0];
    if (file) {
      upload.mutate(file, {
        onSuccess: () => {
          toast.success("Uploaded.");
          form.reset();
          setUploading(false);
        },
      });
    }
  }

  const hasFiles = files.data && files.data.length > 0;
  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-5">
      <PageHeader
        title={<span id="folder-title">{title}</span>}
        hideTitle={hideTitle}
        actions={
          hasFiles || canEdit ? (
            <>
              {hasFiles && (
                <Button asChild>
                  <a href={areaZipUrl(workspace.id, area)} download>
                    Download all as zip
                  </a>
                </Button>
              )}
              {canEdit && (
                <Button
                  type="button"
                  variant="primary"
                  onClick={() => setUploading(true)}
                >
                  <Upload aria-hidden />
                  Upload
                </Button>
              )}
            </>
          ) : undefined
        }
      />
      {files.isPending ? (
        <Loading className="p-0" />
      ) : files.isError ? (
        <ErrorText>Could not load the files: {files.error.message}</ErrorText>
      ) : files.data.length === 0 ? (
        <EmptyState>
          No files yet. Files of {ownerName} will be listed here.
        </EmptyState>
      ) : (
        <TableCard>
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col" className="w-24">
                  Type
                </TableHead>
                <TableHead scope="col" className="w-56">
                  Status
                </TableHead>
                <TableHead scope="col" className="w-24 text-right">
                  Size
                </TableHead>
                <TableHead scope="col" className="w-36">
                  Updated
                </TableHead>
                {canEdit && (
                  <TableHead scope="col" className="w-16">
                    <span className="sr-only">Actions</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {files.data.map((file) => {
                const author = file.updated_by && authors.get(file.updated_by);
                return (
                  <TableRow key={file.id}>
                    <TableCell className={monoClasses}>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <a
                            href={fileDownloadUrl(workspace.id, file.id)}
                            download={file.name}
                            className={`${linkClasses} block truncate`}
                          >
                            {file.name}
                          </a>
                        </TooltipTrigger>
                        <TooltipContent className={monoClasses}>
                          {file.name}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge>{typeLabel(file.mime)}</Badge>
                        </TooltipTrigger>
                        <TooltipContent className={monoClasses}>
                          {file.mime}
                        </TooltipContent>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <StatusCell file={file} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatSize(file.size)}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <span>
                          {new Date(file.updated_at).toLocaleDateString()}
                        </span>
                        <span className="truncate text-xs text-muted-foreground">
                          {author || "—"}
                        </span>
                      </div>
                    </TableCell>
                    {canEdit && (
                      <TableCell className="text-right">
                        <RowActions
                          workspaceId={workspace.id}
                          area={area}
                          file={file}
                          onEdit={() => setEditing(file.id)}
                        />
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableCard>
      )}
      <Dialog
        open={editedFile !== undefined}
        onOpenChange={(open) => !open && setEditing(null)}
      >
        <DialogContent className="w-full max-w-3xl">
          {editedFile && (
            <TextEditor
              key={editedFile.id}
              workspaceId={workspace.id}
              area={area}
              file={editedFile}
              onClose={() => setEditing(null)}
            />
          )}
        </DialogContent>
      </Dialog>
      {canEdit ? (
        <Dialog open={uploading} onOpenChange={setUploading}>
          <DialogContent>
            <form
              className="flex flex-col gap-3.5"
              onSubmit={onSubmit}
              aria-label="Upload document"
            >
              <DialogHeader>
                <DialogTitle>Upload a document</DialogTitle>
                <DialogDescription>
                  PDF, DOCX, XLSX, Markdown, SQL, YAML, CSV, JSON, text or an
                  image, up to 25 MB. A file with the same name is replaced.
                </DialogDescription>
              </DialogHeader>
              <label className={labelClasses}>
                File
                <input
                  type="file"
                  name="file"
                  className="text-sm font-normal file:mr-2 file:h-8 file:rounded-md file:border file:border-border-strong file:bg-surface-raised file:px-3 file:font-medium file:text-foreground hover:file:bg-muted"
                  accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.sql,.yaml,.yml,.csv,.json,.png,.jpg,.jpeg,.gif,.webp"
                />
              </label>
              {upload.isError && <ErrorText>{upload.error.message}</ErrorText>}
              <DialogFooter>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={upload.isPending}
                >
                  Upload
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      ) : (
        <Hint>Only owners and editors can upload documents.</Hint>
      )}
    </section>
  );
}
