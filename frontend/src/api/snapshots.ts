// Metadata extraction into Snapshots (spec stories 45, 46).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { jobsKey } from "./jobs";
import type { components } from "./schema";

export type SnapshotSummary = components["schemas"]["SnapshotSummary"];

export const snapshotsKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "snapshots"] as const;

/** The Source System's Snapshots, newest first. */
export function useSnapshots(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: snapshotsKey(workspaceId, systemId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/snapshots",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** Start an extraction job; fails with `connection_missing` (409) without a Connection. */
export function useStartExtraction(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/extractions",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
  });
}
