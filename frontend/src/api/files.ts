// Files and documents in a Source System's file area (spec stories 64, 67).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type WorkspaceFile = components["schemas"]["WorkspaceFile"];

const filesKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "files"] as const;

/** Where a member downloads a file: the API serves it as an attachment. */
export function fileDownloadUrl(workspaceId: string, fileId: string): string {
  return `/api/v1/workspaces/${workspaceId}/files/${fileId}/download`;
}

/** Every file of the Source System's file area, ordered by name (all pages). */
export function useSystemFiles(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: filesKey(workspaceId, systemId),
    queryFn: async () => {
      const files: WorkspaceFile[] = [];
      let cursor: string | undefined;
      do {
        const { data, error, response } = await client.GET(
          "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
          {
            params: {
              path: { workspace_id: workspaceId, system_id: systemId },
              query: { limit: 100, ...(cursor ? { cursor } : {}) },
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

/**
 * Upload a document; fails with `file_too_large` (413), `unsupported_file_type` (415)
 * or `invalid_file_name` (422). A file with the same name is replaced.
 */
export function useUploadFile(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/files",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          // The generated type names the field `string`; it is sent as multipart form data.
          body: { file: file as unknown as string },
          bodySerializer: () => {
            const form = new FormData();
            form.append("file", file);
            return form;
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: filesKey(workspaceId, systemId) }),
  });
}
