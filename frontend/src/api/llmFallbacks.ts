// Admin: the ordered fallback models of the agent and light roles.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type LlmFallbackRole = components["schemas"]["LlmRoleFallbacks"]["role"];
export type LlmFallbackList = components["schemas"]["LlmFallbackList"];

const fallbacksKey = ["admin", "llm", "fallbacks"] as const;

export function useLlmFallbacks() {
  const client = useApiClient();
  return useQuery({
    queryKey: fallbacksKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/fallbacks",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Replaces a role's whole ordered list. */
export function useSetLlmFallbacks() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      role: LlmFallbackRole;
      modelIds: string[];
    }) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/llm/fallbacks/{role}",
        {
          params: { path: { role: input.role } },
          body: { model_ids: input.modelIds },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) => queryClient.setQueryData(fallbacksKey, data),
  });
}
