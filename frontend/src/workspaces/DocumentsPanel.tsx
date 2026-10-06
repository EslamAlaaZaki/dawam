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
    <form className="form" onSubmit={onSubmit} aria-label={`Edit ${file.name}`}>
      <h4>Edit {file.name}</h4>
      {text.isPending ? (
        <p>Loading…</p>
      ) : text.isError ? (
        <p role="alert">Could not load the file: {text.error.message}</p>
      ) : (
        <label>
          Content
          <textarea
            rows={16}
            spellCheck={false}
            value={draft ?? text.data}
            onChange={(event) => setDraft(event.target.value)}
          />
        </label>
      )}
      <p className="form-hint">
        Saving overwrites the file; there is no version history.
      </p>
      {save.isError && (
        <p className="form-error" role="alert">
          {save.error.message}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={!text.isSuccess || save.isPending}>
          Save
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
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
    <span>
      <input
        type="file"
        aria-label={`Replace ${file.name}`}
        onChange={(event) => {
          const chosen = event.target.files?.[0];
          if (chosen) {
            replace.mutate(chosen);
          }
        }}
      />
      {replace.isError && (
        <span className="form-error" role="alert">
          {replace.error.message}
        </span>
      )}
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
      upload.mutate(file, { onSuccess: () => form.reset() });
    }
  }

  return (
    <section aria-labelledby="folder-title">
      <h3 id="folder-title">{title}</h3>
      {files.isPending ? (
        <p>Loading…</p>
      ) : files.isError ? (
        <p role="alert">Could not load the files: {files.error.message}</p>
      ) : files.data.length === 0 ? (
        <p className="empty-state">
          No files yet. Files of {ownerName} will be listed here.
        </p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Type</th>
              <th scope="col">Size</th>
              <th scope="col">Text</th>
              <th scope="col">Author</th>
              <th scope="col">Updated</th>
              {canEdit && <th scope="col">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {files.data.map((file) => (
              <tr key={file.id}>
                <td>
                  <a
                    href={fileDownloadUrl(workspace.id, file.id)}
                    download={file.name}
                  >
                    {file.name}
                  </a>
                </td>
                <td>{file.mime}</td>
                <td>{formatSize(file.size)}</td>
                <td>{TEXT_STATUS[file.text_status] ?? file.text_status}</td>
                <td>
                  {(file.updated_by && authors.get(file.updated_by)) || "—"}
                </td>
                <td>{new Date(file.updated_at).toLocaleDateString()}</td>
                {canEdit && (
                  <td>
                    {isEditable(file) ? (
                      <button type="button" onClick={() => setEditing(file.id)}>
                        Edit {file.name}
                      </button>
                    ) : (
                      <ReplaceControl
                        workspaceId={workspace.id}
                        area={area}
                        file={file}
                      />
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {files.data && files.data.length > 0 && (
        <p>
          <a href={areaZipUrl(workspace.id, area)} download>
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
        <form className="form" onSubmit={onSubmit} aria-label="Upload document">
          <h4>Upload a document</h4>
          <label>
            File
            <input
              type="file"
              name="file"
              accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.sql,.yaml,.yml,.csv,.json,.png,.jpg,.jpeg,.gif,.webp"
            />
          </label>
          <p className="form-hint">
            PDF, DOCX, XLSX, Markdown, SQL, YAML, CSV, JSON, text or an image,
            up to 25 MB. A file with the same name is replaced.
          </p>
          {upload.isError && (
            <p className="form-error" role="alert">
              {upload.error.message}
            </p>
          )}
          {upload.isSuccess && <p role="status">Uploaded.</p>}
          <div className="form-actions">
            <button type="submit" disabled={upload.isPending}>
              Upload
            </button>
          </div>
        </form>
      ) : (
        <p>Only owners and editors can upload documents.</p>
      )}
    </section>
  );
}
