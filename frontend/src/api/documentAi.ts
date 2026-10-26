// Document AI settings (24.1): the RAG pipeline's knobs. Admins set the installation's values
// and the bounds owners override within; owners override some knobs per Workspace.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type KnobInfo = components["schemas"]["KnobInfo"];
export type SettingValue = KnobInfo["default"];
export type InstallationDocumentAi =
  components["schemas"]["InstallationDocumentAiSettings"];
export type InstallationDocumentAiRequest =
  components["schemas"]["InstallationDocumentAiRequest"];
export type WorkspaceDocumentAi =
  components["schemas"]["WorkspaceDocumentAiSettings"];
export type WorkspaceDocumentAiRequest =
  components["schemas"]["WorkspaceDocumentAiRequest"];

const installationKey = ["admin", "document-ai"] as const;
const workspaceKey = (workspaceId: string) =>
  ["workspace", workspaceId, "document-ai"] as const;

export function useInstallationDocumentAi({
  enabled = true,
}: { enabled?: boolean } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: installationKey,
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/document-ai",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Replace the installation's values and bounds (admins only). */
export function useSetInstallationDocumentAi() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: InstallationDocumentAiRequest) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/llm/document-ai",
        { body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(installationKey, data);
      // Every Workspace's inherited and effective values may have changed.
      void queryClient.invalidateQueries({ queryKey: ["workspace"] });
    },
  });
}

/** Index every Workspace's documents again, one background job each (admins only). */
export function useReindexEverywhere() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST(
        "/api/v1/admin/llm/document-ai/reindex",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
  });
}

/** The Workspace's overrides and effective settings (any member). */
export function useWorkspaceDocumentAi(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: workspaceKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/document-ai",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Replace the Workspace's overrides (owners only). */
export function useSetWorkspaceDocumentAi(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: WorkspaceDocumentAiRequest) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/document-ai",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) =>
      queryClient.setQueryData(workspaceKey(workspaceId), data),
  });
}
