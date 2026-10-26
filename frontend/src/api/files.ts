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

/** Whether a file's indexing job is still to finish (it ends as `indexed` or `failed`). */
export function isIndexing(file: WorkspaceFile): boolean {
  return file.index_status !== "indexed" && file.index_status !== "failed";
}

/** How often the file list is fetched again while a file is still being indexed. */
const INDEXING_POLL_MS = 2000;

/** Every file of the file area, ordered by name (all pages). Refreshes while any file is
 * still being indexed. */
export function useAreaFiles(workspaceId: string, area: FileArea) {
  const client = useApiClient();
  return useQuery({
    queryKey: filesKey(workspaceId, area),
    refetchInterval: (query) =>
      query.state.status !== "error" && query.state.data?.some(isIndexing)
        ? INDEXING_POLL_MS
        : false,
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

/** Where a member downloads a Source System's data dictionary (XLSX). */
export function dataDictionaryUrl(
  workspaceId: string,
  systemId: string,
): string {
  return `/api/v1/workspaces/${workspaceId}/systems/${systemId}/data-dictionary`;
}

/**
 * Save a Source System's data dictionary into its file area (owners and editors). Fails
 * with 404 before the first Snapshot; a dictionary saved earlier is replaced.
 */
export function useSaveDataDictionary(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const area: FileArea = { systemId };
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files/data-dictionary",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
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

export type FileIndexStatus = components["schemas"]["FileIndexStatus"];

const indexStatusKey = (workspaceId: string) =>
  ["workspace", workspaceId, "document-index-status"] as const;

/** Where indexing stands for each file of the Workspace; refreshes while any is unfinished. */
export function useIndexStatus(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: indexStatusKey(workspaceId),
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      query.state.data?.some(
        (row) =>
          row.index_status !== "indexed" && row.index_status !== "failed",
      )
        ? INDEXING_POLL_MS
        : false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/documents/index-status",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** Index one file again (owners and editors). */
export function useReindexDocument(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (fileId: string) => {
      const { error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/documents/{file_id}/reindex",
        { params: { path: { workspace_id: workspaceId, file_id: fileId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: indexStatusKey(workspaceId) }),
  });
}

/** Index every file of the Workspace again, as a background job (owners and editors). */
export function useReindexAllDocuments(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/documents/reindex",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: indexStatusKey(workspaceId) }),
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

export type RetrievalTrace = components["schemas"]["RetrievalTrace"];
export type TraceEntry = components["schemas"]["TraceEntry"];

/** The retrieval playground: runs a search and returns each stage's ranking and scores. */
export function useExplainSearch(workspaceId: string) {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (q: string) => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/documents/search/explain",
        { params: { path: { workspace_id: workspaceId }, query: { q } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
