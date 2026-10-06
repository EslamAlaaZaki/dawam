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
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
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
      queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
  });
}

export type SourceSchema = components["schemas"]["SourceSchema"];
export type SchemaSearchHit = components["schemas"]["SearchHit"];

export const sourceSchemaKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "source-schema"] as const;

/** The Source Schema to browse: the latest Snapshot plus the objects it no longer has
 * (spec story 54). Only asked for once the system has a Snapshot. */
export function useSourceSchema(
  workspaceId: string,
  systemId: string,
  enabled: boolean,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: sourceSchemaKey(workspaceId, systemId),
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/schema",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Names containing `query`, in the latest Snapshot; nothing is asked for an empty query. */
export function useSchemaSearch(
  workspaceId: string,
  systemId: string,
  query: string,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [
      ...sourceSchemaKey(workspaceId, systemId),
      "search",
      query,
    ] as const,
    enabled: query !== "",
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/schema/search",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { q: query },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

export type SnapshotDiff = components["schemas"]["SnapshotDiff"];

/** What changed from Snapshot `from` to Snapshot `to` (spec story 52); asked for only once
 * both are chosen. */
export function useSnapshotDiff(
  workspaceId: string,
  systemId: string,
  from: string | null,
  to: string | null,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [
      ...snapshotsKey(workspaceId, systemId),
      "diff",
      from,
      to,
    ] as const,
    enabled: from != null && to != null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/snapshots/{snapshot_id}/diff/{against_id}",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              snapshot_id: to ?? "",
              against_id: from ?? "",
            },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
