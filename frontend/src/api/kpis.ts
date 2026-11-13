// The KPI catalog and its review (spec stories 73-77).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Kpi = components["schemas"]["Kpi"];
export type KpiStatus = Kpi["status"];
export type KpiReviewState = Kpi["review_state"];
export type KpiFeasibility = NonNullable<Kpi["feasibility"]>;
export type KpiLink = components["schemas"]["KpiLink"];
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

/**
 * Every KPI of the scope, ordered by name (all pages): the KPIs tab (live and accepted), or
 * with `review` "all" the suggested and rejected ones too (owners and editors only).
 */
export function useKpis(
  workspaceId: string,
  scope: KpiScope,
  review: "catalog" | "all" = "catalog",
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...kpisKey(workspaceId), scope.systemId ?? "dw", review],
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
                review,
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

/** Accept a suggested (or rejected) KPI, or reject a suggested one; owners and editors. */
export function useDecideKpi(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ kpiId, action }: { kpiId: string; action: "accept" | "reject" }) => {
      const path =
        action === "accept"
          ? "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}/accept"
          : "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}/reject";
      const { data, error, response } = await client.POST(path, {
        params: { path: { workspace_id: workspaceId, kpi_id: kpiId } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: kpisKey(workspaceId) }),
  });
}

/** The DW columns a KPI links to (asked only while `enabled`, e.g. its details are open). */
export function useKpiLinks(workspaceId: string, kpiId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...kpisKey(workspaceId), kpiId, "links"],
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/kpis/{kpi_id}/links",
        { params: { path: { workspace_id: workspaceId, kpi_id: kpiId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}
