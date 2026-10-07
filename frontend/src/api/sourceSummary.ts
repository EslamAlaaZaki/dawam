// The Source System dashboard (spec story 63).
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type SourceSummary = components["schemas"]["SourceSummary"];

export const sourceSummaryKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "summary"] as const;

/** The counts of a Source System's analysis, computed on the server (any member). */
export function useSourceSummary(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: sourceSummaryKey(workspaceId, systemId),
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/summary",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
