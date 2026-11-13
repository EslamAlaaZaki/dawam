// AI KPI suggestions for one Source System, as a background job (spec story 75).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type KpiSuggestionJob = components["schemas"]["KpiSuggestionJob"];

const ACTIVE: ReadonlySet<KpiSuggestionJob["status"]> = new Set(["queued", "running", "paused"]);
const REFRESH_MS = 3_000;

/** Whether the job has not ended yet. */
export function isRunning(job: KpiSuggestionJob | null | undefined): boolean {
  return job != null && ACTIVE.has(job.status);
}

const jobKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "kpi-suggestions", systemId] as const;

/** The Source System's newest suggestion job (owners and editors); refreshed while it runs. */
export function useKpiSuggestionJob(workspaceId: string, systemId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: jobKey(workspaceId, systemId),
    enabled,
    retry: false,
    refetchInterval: (query) => (isRunning(query.state.data) ? REFRESH_MS : false),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/kpi-suggestions",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.job;
    },
  });
}

/** Start the job; fails with `kpi_suggestions_running` (409) while one runs for the system. */
export function useStartKpiSuggestions(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/kpi-suggestions",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (job) => queryClient.setQueryData(jobKey(workspaceId, systemId), job),
    onSettled: () => queryClient.invalidateQueries({ queryKey: jobKey(workspaceId, systemId) }),
  });
}
