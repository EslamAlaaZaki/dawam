// The KPI catalog (spec stories 73, 74).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Kpi = components["schemas"]["Kpi"];
export type KpiStatus = Kpi["status"];
export type KpiTarget = components["schemas"]["Target"];
export type CreateKpiRequest = components["schemas"]["CreateKpiRequest"];
export type UpdateKpiRequest = components["schemas"]["UpdateKpiRequest"];

export const KPI_STATUS_LABELS: Record<KpiStatus, string> = {
  draft: "Draft",
  in_review: "In review",
  approved: "Approved",
};

/** Where a KPI is documented: under one Source System, or under the Data Warehouse. */
export interface KpiScope {
  systemId: string | null;
}

const kpisKey = (workspaceId: string) => ["workspace", workspaceId, "kpis"] as const;

/** Every KPI of the scope, ordered by name (all pages). */
export function useKpis(workspaceId: string, scope: KpiScope) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...kpisKey(workspaceId), scope.systemId ?? "dw"],
    queryFn: async () => {
      const kpis: Kpi[] = [];
      let cursor: string | undefined;
      do {
        const { data, error, response } = await client.GET(
          "/api/v1/workspaces/{workspace_id}/kpis",
          {
            params: {
              path: { workspace_id: workspaceId },
              query: {
                limit: 100,
                ...(scope.systemId ? { source_system_id: scope.systemId } : { data_warehouse: true }),
                ...(cursor ? { cursor } : {}),
              },
            },
          },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        kpis.push(...data.items);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
      return kpis;
    },
  });
}

/** Document a KPI (a draft); fails with `invalid_kpi` (422). */
export function useCreateKpi(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateKpiRequest) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/kpis",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: kpisKey(workspaceId) }),
  });
}

/** Edit a KPI or change its status; a stale `version` fails with `ApiError` 409. */
export function useUpdateKpi(workspaceId: string, kpiId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: UpdateKpiRequest) => {
      const { data, error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}",
        { params: { path: { workspace_id: workspaceId, kpi_id: kpiId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: kpisKey(workspaceId) }),
  });
}

export function useDeleteKpi(workspaceId: string, kpiId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}",
        { params: { path: { workspace_id: workspaceId, kpi_id: kpiId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: kpisKey(workspaceId) }),
  });
}
