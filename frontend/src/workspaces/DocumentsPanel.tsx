"use client";

import { useState, type FormEvent } from "react";

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
import { Button } from "../components/ui/button";
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
  Actions,
  EmptyState,
  ErrorText,
  Hint,
  TableCard,
  formCardClasses,
  h3Classes,
  h4Classes,
  labelClasses,
  linkClasses,
  monoClasses,
} from "./parts";

const TEXT_STATUS: Record<string, string> = {
  extracted: "Text extracted",
  no_text_found: "No text found",
  none: "—",
};

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
      className={formCardClasses.replace("max-w-xl", "max-w-3xl")}
      onSubmit={onSubmit}
      aria-label={`Edit ${file.name}`}
    >
      <h4 className={h4Classes}>Edit {file.name}</h4>
      {text.isPending ? (
        <p className="text-sand-11">Loading…</p>
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
      <Actions>
        <Button
          type="submit"
          variant="primary"
          disabled={!text.isSuccess || save.isPending}
        >
          Save
        </Button>
        <Button type="button" onClick={onClose}>
          Cancel
        </Button>
      </Actions>
    </form>
  );
}

function ReplaceControl({
  workspaceId,
  area,
  file,
}: {
  workspaceId: string;
  area: FileArea;
  file: WorkspaceFile;
}) {
  const replace = useReplaceFile(workspaceId, area, file.id);
  return (
    <span className="flex flex-col gap-1">
      <input
        type="file"
        className="text-sm file:mr-2 file:h-[26px] file:rounded-md file:border file:border-sand-6 file:bg-sand-1 file:px-2 file:font-medium file:text-sand-12 hover:file:bg-sand-3"
        aria-label={`Replace ${file.name}`}
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) {
            replace.mutate(chosen);
          }
        }}
      />
      {replace.isError && <ErrorText>{replace.error.message}</ErrorText>}
    </span>
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
}: {
  workspace: Workspace;
  area: FileArea;
  title: string;
  ownerName: string;
}) {
  const files = useAreaFiles(workspace.id, area);
  const members = useMembers(workspace.id);
  const upload = useUploadFile(workspace.id, area);
  const [editing, setEditing] = useState<string | null>(null);
  const canEdit = allows(workspace, "file.upload");
  const authors = new Map(
    (members.data ?? []).map((m) => [m.user_id, m.display_name]),
  );

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
        },
      });
    }
  }

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <h3 id="folder-title" className={h3Classes}>
        {title}
      </h3>
      {files.isPending ? (
        <p className="text-sand-11">Loading…</p>
      ) : files.isError ? (
        <ErrorText>Could not load the files: {files.error.message}</ErrorText>
      ) : files.data.length === 0 ? (
        <EmptyState>
          No files yet. Files of {ownerName} will be listed here.
        </EmptyState>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col">Type</TableHead>
                <TableHead scope="col">Size</TableHead>
                <TableHead scope="col">Text</TableHead>
                <TableHead scope="col">Author</TableHead>
                <TableHead scope="col">Updated</TableHead>
                {canEdit && <TableHead scope="col">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {files.data.map((file) => (
                <TableRow key={file.id}>
                  <TableCell className={monoClasses}>
                    <a
                      href={fileDownloadUrl(workspace.id, file.id)}
                      download={file.name}
                      className={linkClasses}
                    >
                      {file.name}
                    </a>
                  </TableCell>
                  <TableCell className={`${monoClasses} text-sand-11`}>
                    {file.mime}
                  </TableCell>
                  <TableCell>{formatSize(file.size)}</TableCell>
                  <TableCell>
                    {TEXT_STATUS[file.text_status] ?? file.text_status}
                  </TableCell>
                  <TableCell>
                    {(file.updated_by && authors.get(file.updated_by)) || "—"}
                  </TableCell>
                  <TableCell>
                    {new Date(file.updated_at).toLocaleDateString()}
                  </TableCell>
                  {canEdit && (
                    <TableCell>
                      {isEditable(file) ? (
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => setEditing(file.id)}
                        >
                          Edit {file.name}
                        </Button>
                      ) : (
                        <ReplaceControl
                          workspaceId={workspace.id}
                          area={area}
                          file={file}
                        />
                      )}
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {files.data && files.data.length > 0 && (
        <p>
          <a
            href={areaZipUrl(workspace.id, area)}
            download
            className={linkClasses}
          >
            Download all as zip
          </a>
        </p>
      )}
      {files.data?.map(
        (file) =>
          editing === file.id && (
            <TextEditor
              key={file.id}
              workspaceId={workspace.id}
              area={area}
              file={file}
              onClose={() => setEditing(null)}
            />
          ),
      )}
      {canEdit ? (
        <form
          className={formCardClasses}
          onSubmit={onSubmit}
          aria-label="Upload document"
        >
          <h4 className={h4Classes}>Upload a document</h4>
          <label className={labelClasses}>
            File
            <input
              type="file"
              name="file"
              className="text-sm font-normal file:mr-2 file:h-8 file:rounded-md file:border file:border-sand-6 file:bg-sand-1 file:px-3 file:font-medium file:text-sand-12 hover:file:bg-sand-3"
              accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.sql,.yaml,.yml,.csv,.json,.png,.jpg,.jpeg,.gif,.webp"
            />
          </label>
          <Hint>
            PDF, DOCX, XLSX, Markdown, SQL, YAML, CSV, JSON, text or an image,
            up to 25 MB. A file with the same name is replaced.
          </Hint>
          {upload.isError && <ErrorText>{upload.error.message}</ErrorText>}
          <Actions>
            <Button type="submit" variant="primary" disabled={upload.isPending}>
              Upload
            </Button>
          </Actions>
        </form>
      ) : (
        <Hint>Only owners and editors can upload documents.</Hint>
      )}
    </section>
  );
}
