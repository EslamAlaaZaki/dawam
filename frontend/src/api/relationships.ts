// Inferred relationships of a Source System (spec story 60): the ER diagram draws the
// accepted ones next to the foreign keys the Snapshot declares.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { jobsKey } from "./jobs";
import type { components } from "./schema";

export type Relationship = components["schemas"]["Relationship"];

export const relationshipsKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "relationships"] as const;

/** The Source System's accepted relationships, whatever their confidence (a member accepted them). */
export function useAcceptedRelationships(
  workspaceId: string,
  systemId: string,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...relationshipsKey(workspaceId, systemId), "accepted"] as const,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { status: "accepted", min_confidence: 0 },
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

export type RelationshipStatus = Relationship["status"];

/** The Source System's relationships in one state, most confident first (any confidence). */
export function useRelationshipsByStatus(
  workspaceId: string,
  systemId: string,
  status: RelationshipStatus,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...relationshipsKey(workspaceId, systemId), status] as const,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { status, min_confidence: 0 },
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

/** Start Relationship Inference (default threshold and sample size) as a background job. */
export function useStartInference(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationship-inference",
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

/** Accept or reject a relationship; every list, and so the diagram, refreshes. */
export function useDecideRelationship(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      decision,
    }: {
      id: string;
      decision: "accept" | "reject";
    }) => {
      const path = {
        workspace_id: workspaceId,
        system_id: systemId,
        relationship_id: id,
      };
      const { data, error, response } =
        decision === "accept"
          ? await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships/{relationship_id}/accept",
              { params: { path } },
            )
          : await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships/{relationship_id}/reject",
              { params: { path } },
            );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: relationshipsKey(workspaceId, systemId),
      }),
  });
}

/** Add a relationship by hand (accepted at once); every list, and so the diagram, refreshes. */
export function useAddRelationship(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { from_column_id: string; to_column_id: string }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: relationshipsKey(workspaceId, systemId),
      }),
  });
}

/** Delete a relationship: a manual one goes, an inferred one is rejected for good. */
export function useDeleteRelationship(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/relationships/{relationship_id}",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              relationship_id: id,
            },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: relationshipsKey(workspaceId, systemId),
      }),
  });
}
