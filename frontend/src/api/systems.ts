// Source Systems of a Workspace (spec story 39).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type SourceSystem = components["schemas"]["SourceSystem"];
export type CreateSourceSystemRequest = components["schemas"]["CreateSourceSystemRequest"];
export type UpdateSourceSystemRequest = components["schemas"]["UpdateSourceSystemRequest"];

const systemsKey = (workspaceId: string) => ["workspace", workspaceId, "systems"] as const;

/** Every Source System of the Workspace, ordered by System Code (all pages). */
export function useSourceSystems(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: systemsKey(workspaceId),
    queryFn: async () => {
      const systems: SourceSystem[] = [];
      let cursor: string | undefined;
      do {
        const { data, error, response } = await client.GET(
          "/api/v1/workspaces/{workspace_id}/systems",
          {
            params: {
              path: { workspace_id: workspaceId },
              query: { limit: 100, ...(cursor ? { cursor } : {}) },
            },
          },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        systems.push(...data.items);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
      return systems;
    },
  });
}

/** Add a Source System; fails with `invalid_system_code` (422) or `system_code_taken` (409). */
export function useCreateSourceSystem(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateSourceSystemRequest) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: systemsKey(workspaceId) }),
  });
}

/** Edit a Source System; a stale `version` fails with `ApiError` 409. */
export function useUpdateSourceSystem(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: UpdateSourceSystemRequest) => {
      const { data, error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: systemsKey(workspaceId) }),
  });
}
