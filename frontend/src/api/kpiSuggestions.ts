// AI KPI suggestions for one Source System, or cross-system ones for the Data Warehouse, as a
// background job (spec story 75).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type KpiSuggestionJob = components["schemas"]["KpiSuggestionJob"];

/** A Source System's suggestions, or the Data Warehouse's (cross-system) ones. */
export type KpiSuggestionScope = { systemId: string } | { dataWarehouse: true };

/** The newest job and whether one may start (a Source System's always may). */
export type KpiSuggestionState = {
  job: KpiSuggestionJob | null;
  available: boolean;
  /** The Workspace's Source Systems; counted for the Data Warehouse only. */
  sourceSystems: number;
  /** Whether the DW model exists (Core or Mart has tables); for the Data Warehouse only. */
  dwModel: boolean;
};

const ACTIVE: ReadonlySet<KpiSuggestionJob["status"]> = new Set(["queued", "running", "paused"]);
const REFRESH_MS = 3_000;

/** Whether the job has not ended yet. */
export function isRunning(job: KpiSuggestionJob | null | undefined): boolean {
  return job != null && ACTIVE.has(job.status);
}

const jobKey = (workspaceId: string, scope: KpiSuggestionScope) =>
  [
    "workspace",
    workspaceId,
    "kpi-suggestions",
    "systemId" in scope ? scope.systemId : "data-warehouse",
  ] as const;

/** The scope's newest suggestion job (owners and editors); refreshed while it runs. */
export function useKpiSuggestionJob(
  workspaceId: string,
  scope: KpiSuggestionScope,
  enabled: boolean,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: jobKey(workspaceId, scope),
    enabled,
    retry: false,
    refetchInterval: (query) => (isRunning(query.state.data?.job) ? REFRESH_MS : false),
    queryFn: async (): Promise<KpiSuggestionState> => {
      if ("systemId" in scope) {
        const { data, error, response } = await client.GET(
          "/api/v1/workspaces/{workspace_id}/systems/{system_id}/kpi-suggestions",
          { params: { path: { workspace_id: workspaceId, system_id: scope.systemId } } },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        return { job: data.job ?? null, available: true, sourceSystems: 0, dwModel: false };
      }
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/kpi-suggestions",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return {
        job: data.job ?? null,
        available: data.available,
        sourceSystems: data.source_systems,
        dwModel: data.dw_model,
      };
    },
  });
}

/**
 * Start the job; fails with `kpi_suggestions_running` (409) while one runs for the scope, and
 * for the Data Warehouse with `kpi_suggestions_unavailable` (409) until it may start.
 */
export function useStartKpiSuggestions(workspaceId: string, scope: KpiSuggestionScope) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const key = jobKey(workspaceId, scope);
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } =
        "systemId" in scope
          ? await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/kpi-suggestions",
              { params: { path: { workspace_id: workspaceId, system_id: scope.systemId } } },
            )
          : await client.POST("/api/v1/workspaces/{workspace_id}/data-warehouse/kpi-suggestions", {
              params: { path: { workspace_id: workspaceId } },
            });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (job) =>
      queryClient.setQueryData<KpiSuggestionState>(key, (old) => ({
        available: true,
        sourceSystems: 0,
        dwModel: false,
        ...old,
        job,
      })),
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });
}
