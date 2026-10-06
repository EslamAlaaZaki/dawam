// File areas of Source Systems and the Data Warehouse: files, documents, in-browser
// editing and zips (spec stories 64, 67, 163-165).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type WorkspaceFile = components["schemas"]["WorkspaceFile"];

/** A file area: a Source System's (by id) or the Data Warehouse's. */
export type FileArea = { systemId: string } | "data-warehouse";

const filesKey = (workspaceId: string, area: FileArea) =>
  area === "data-warehouse"
    ? (["workspace", workspaceId, "data-warehouse", "files"] as const)
    : (["workspace", workspaceId, "systems", area.systemId, "files"] as const);

function areaPath(workspaceId: string, area: FileArea): string {
  const base = `/api/v1/workspaces/${workspaceId}`;
  return area === "data-warehouse"
    ? `${base}/data-warehouse`
    : `${base}/systems/${area.systemId}`;
}

/** Where a member downloads the whole file area as a zip. */
export function areaZipUrl(workspaceId: string, area: FileArea): string {
  return `${areaPath(workspaceId, area)}/files/download`;
}

/** Types the browser edits as text (the rest are downloaded and replaced). */
const TEXT_MIMES = new Set([
  "text/markdown",
  "text/plain",
  "application/sql",
  "application/yaml",
  "text/csv",
  "application/json",
]);

export function isEditable(file: WorkspaceFile): boolean {
  return TEXT_MIMES.has(file.mime);
}

/** Where a member downloads a file: the API serves it as an attachment. */
export function fileDownloadUrl(workspaceId: string, fileId: string): string {
  return `/api/v1/workspaces/${workspaceId}/files/${fileId}/download`;
}

/** Every file of the file area, ordered by name (all pages). */
export function useAreaFiles(workspaceId: string, area: FileArea) {
  const client = useApiClient();
  return useQuery({
    queryKey: filesKey(workspaceId, area),
    queryFn: async () => {
      const files: WorkspaceFile[] = [];
      let cursor: string | undefined;
      do {
        const query = { limit: 100, ...(cursor ? { cursor } : {}) };
        const { data, error, response } =
          area === "data-warehouse"
            ? await client.GET(
                "/api/v1/workspaces/{workspace_id}/data-warehouse/files",
                {
                  params: { path: { workspace_id: workspaceId }, query },
                },
              )
            : await client.GET(
                "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
                {
                  params: {
                    path: {
                      workspace_id: workspaceId,
                      system_id: area.systemId,
                    },
                    query,
                  },
                },
              );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        files.push(...data.items);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
      return files;
    },
  });
}

function multipart(file: File) {
  return {
    // The generated type names the field `string`; it is sent as multipart form data.
    body: { file: file as unknown as string },
    bodySerializer: () => {
      const form = new FormData();
      form.append("file", file);
      return form;
    },
  };
}

/**
 * Upload a document; fails with `file_too_large` (413), `unsupported_file_type` (415)
 * or `invalid_file_name` (422). A file with the same name is replaced.
 */
export function useUploadFile(workspaceId: string, area: FileArea) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const { data, error, response } =
        area === "data-warehouse"
          ? await client.POST(
              "/api/v1/workspaces/{workspace_id}/data-warehouse/files",
              {
                params: { path: { workspace_id: workspaceId } },
                ...multipart(file),
              },
            )
          : await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
              {
                params: {
                  path: { workspace_id: workspaceId, system_id: area.systemId },
                },
                ...multipart(file),
              },
            );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: filesKey(workspaceId, area) }),
  });
}

/** The text of a file, for the editor. */
export function useFileText(workspaceId: string, fileId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", workspaceId, "file-text", fileId],
    gcTime: 0,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/content",
        { params: { path: { workspace_id: workspaceId, file_id: fileId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.content;
    },
  });
}

/** Overwrite a text file in place (no history); fails with `file_too_large` or `not_editable`. */
export function useSaveFileText(
  workspaceId: string,
  area: FileArea,
  fileId: string,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (content: string) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/content",
        {
          params: { path: { workspace_id: workspaceId, file_id: fileId } },
          body: { content },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: filesKey(workspaceId, area) }),
  });
}

/** Replace a file's content with an upload of the same type, keeping its name. */
export function useReplaceFile(
  workspaceId: string,
  area: FileArea,
  fileId: string,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/files/{file_id}/replace",
        {
          params: { path: { workspace_id: workspaceId, file_id: fileId } },
          ...multipart(file),
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: filesKey(workspaceId, area) }),
  });
}
