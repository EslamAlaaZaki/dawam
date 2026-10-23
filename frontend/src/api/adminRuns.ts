// Admin: the assistant run explorer.
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type RunSummary = components["schemas"]["RunSummaryOut"];
export type RunDetail = components["schemas"]["RunDetailOut"];
export type RunStep = components["schemas"]["RunStepOut"];

export interface RunFilters {
  workspace_id?: string;
  member_id?: string;
  status?: string;
  started_from?: string;
  started_to?: string;
}

const key = ["admin", "assistant-runs"] as const;

/** Runs across all Workspaces, newest first. */
export function useAssistantRuns(filters: RunFilters) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...key, filters],
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/assistant/runs",
        { params: { query: filters } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** One run with its timeline and feedback; waits until a run is picked. */
export function useAssistantRun(runId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...key, runId],
    enabled: runId !== null,
    queryFn: async (): Promise<RunDetail> => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/assistant/runs/{run_id}",
        { params: { path: { run_id: runId! } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
