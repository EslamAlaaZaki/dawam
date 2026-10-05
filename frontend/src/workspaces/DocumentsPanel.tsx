"use client";

import type { FormEvent } from "react";

import { fileDownloadUrl, useSystemFiles, useUploadFile, type WorkspaceFile } from "../api/files";
import type { SourceSystem } from "../api/systems";
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

function FileRow({ workspaceId, file }: { workspaceId: string; file: WorkspaceFile }) {
  return (
    <tr>
      <td>
        <a href={fileDownloadUrl(workspaceId, file.id)} download={file.name}>
          {file.name}
        </a>
      </td>
      <td>{file.mime}</td>
      <td>{formatSize(file.size)}</td>
      <td>{TEXT_STATUS[file.text_status] ?? file.text_status}</td>
      <td>{new Date(file.updated_at).toLocaleDateString()}</td>
    </tr>
  );
}

/**
 * The Documents folder of a Source System: its uploaded files (any member downloads
 * them) and, for owners and editors, a form to upload one.
 */
export function DocumentsPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  const files = useSystemFiles(workspace.id, system.id);
  const upload = useUploadFile(workspace.id, system.id);

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
      <h3 id="folder-title">Documents</h3>
      {files.isPending ? (
        <p>Loading…</p>
      ) : files.isError ? (
        <p role="alert">Could not load the documents: {files.error.message}</p>
      ) : files.data.length === 0 ? (
        <p className="empty-state">
          No documents yet. Uploaded documents of {system.name} will be listed here.
        </p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Type</th>
              <th scope="col">Size</th>
              <th scope="col">Text</th>
              <th scope="col">Updated</th>
            </tr>
          </thead>
          <tbody>
            {files.data.map((file) => (
              <FileRow key={file.id} workspaceId={workspace.id} file={file} />
            ))}
          </tbody>
        </table>
      )}
      {allows(workspace, "file.upload") ? (
        <form className="form" onSubmit={onSubmit} aria-label="Upload document">
          <h4>Upload a document</h4>
          <label>
            File
            <input
              type="file"
              name="file"
              accept=".pdf,.docx,.xlsx,.md,.markdown,.txt,.png,.jpg,.jpeg,.gif,.webp"
            />
          </label>
          <p className="form-hint">
            PDF, DOCX, XLSX, Markdown, text or an image, up to 25 MB. A file with the same name is
            replaced.
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
