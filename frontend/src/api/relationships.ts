// Inferred relationships of a Source System (spec story 60): the ER diagram draws the
// accepted ones next to the foreign keys the Snapshot declares.
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Relationship = components["schemas"]["Relationship"];

export const relationshipsKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "relationships"] as const;

/** The Source System's accepted relationships, whatever their confidence (a member accepted them). */
export function useAcceptedRelationships(workspaceId: string, systemId: string) {
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
