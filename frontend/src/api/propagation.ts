// "Fix impacts": recompute the propagation Change Set from what is broken (spec story 113a).
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type FixImpactsResult = components["schemas"]["FixImpactsResult"];

export function useFixImpacts(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/fix-impacts",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["workspace", workspaceId, "change-sets"],
      }),
  });
}
