// A Workspace's AI settings: its Workspace Models (spec §6.18, stories 160, 161).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type WorkspaceAiSettings = components["schemas"]["WorkspaceAiSettings"];
export type WorkspaceAiSettingsRequest =
  components["schemas"]["WorkspaceAiSettingsRequest"];

export type WorkspaceModel = components["schemas"]["WorkspaceModel"];
export type ModelChoice = components["schemas"]["ModelChoice"];
export type NewWorkspaceModels = components["schemas"]["NewWorkspaceModels"];

const aiKey = (workspaceId: string) =>
  ["workspace", workspaceId, "ai-settings"] as const;

/** The settings, the Workspace Models and the models an owner may choose per role. */
export function useWorkspaceAiSettings(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: aiKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/ai-settings",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Replace the settings (owners only). */
export function useSetWorkspaceAiSettings(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: WorkspaceAiSettingsRequest) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/ai-settings",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) => queryClient.setQueryData(aiKey(workspaceId), data),
  });
}

/** The models the "New workspace" form offers for each role. */
export function useNewWorkspaceModels() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["ai", "new-workspace-models"] as const,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/ai/new-workspace-models",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
