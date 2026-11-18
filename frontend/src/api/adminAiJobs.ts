// Admin: the background AI jobs and document searches (the "Jobs & search" tab).
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type AiJob = components["schemas"]["AiJobOut"];
export type AiJobDetail = components["schemas"]["AiJobDetailOut"];

export interface AiJobFilters {
  workspace_id?: string;
  feature?: string;
  status?: string;
  started_from?: string;
  started_to?: string;
}

const key = ["admin", "ai-jobs"] as const;

/** Model calls made outside the assistant, newest first. */
export function useAiJobs(filters: AiJobFilters) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...key, filters],
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/admin/ai/jobs", {
        params: { query: filters },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** One job (or one Workspace's searches of a day) with its calls per model and failed calls. */
export function useAiJob(jobKey: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...key, jobKey],
    enabled: jobKey !== null,
    queryFn: async (): Promise<AiJobDetail> => {
      const { data, error, response } = await client.GET("/api/v1/admin/ai/jobs/{key}", {
        params: { path: { key: jobKey! } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
