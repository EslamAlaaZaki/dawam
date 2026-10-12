// The PII view (spec §6.13): the DW columns derived from confirmed PII. The star diagram
// marks them.
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type PiiViewReport = components["schemas"]["PiiViewReport"];

export const piiViewKey = (workspaceId: string) =>
  ["workspace", workspaceId, "pii-view"] as const;

export function usePiiView(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: piiViewKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/pii",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
