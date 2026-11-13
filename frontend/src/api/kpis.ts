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

/**
 * Where a KPIs list reads from: one Source System (`systemId`), the Data Warehouse's own
 * KPIs (`systemId` null), or with `workspaceWide` every KPI in the Workspace (the Data
 * Warehouse page), which also brings the rule-based suggestions.
 */
export interface KpiScope {
  systemId: string | null;
  workspaceWide?: boolean;
}

export type RuleSuggestion = components["schemas"]["KpiSuggestion"];
export type RejectedRuleSuggestion = components["schemas"]["RejectedKpiSuggestion"];

/** What one list request returns: the KPIs and, for the whole Workspace, the rule suggestions. */
export interface KpiList {
  kpis: Kpi[];
  ruleSuggestions: RuleSuggestion[];
  rejectedRuleSuggestions: RejectedRuleSuggestion[];
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
  return useKpiListQuery(workspaceId, scope, review, (list) => list.kpis);
}

/** The same request as `useKpis`, with the rule suggestions of the Workspace-wide scope. */
export function useKpiList(
  workspaceId: string,
  scope: KpiScope,
  review: "catalog" | "all" = "catalog",
) {
  return useKpiListQuery(workspaceId, scope, review, (list) => list);
}

function useKpiListQuery<T>(
  workspaceId: string,
  scope: KpiScope,
  review: "catalog" | "all",
  select: (list: KpiList) => T,
) {
  const client = useApiClient();
  const withRules = scope.workspaceWide === true && review === "all";
  return useQuery({
    queryKey: [
      ...kpisKey(workspaceId),
      scope.workspaceWide ? "workspace" : (scope.systemId ?? "dw"),
      review,
    ],
    select,
    queryFn: async (): Promise<KpiList> => {
      const list: KpiList = { kpis: [], ruleSuggestions: [], rejectedRuleSuggestions: [] };
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
                ...(withRules ? { rule_suggestions: true } : {}),
                ...(scope.workspaceWide
                  ? {}
                  : scope.systemId
                    ? { source_system_id: scope.systemId }
                    : { data_warehouse: true }),
                ...(cursor ? { cursor } : {}),
              },
            },
          },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        list.kpis.push(...data.items);
        // The suggestions come with the first page only.
        list.ruleSuggestions.push(...data.rule_suggestions);
        list.rejectedRuleSuggestions.push(...data.rejected_rule_suggestions);
        cursor = data.next_cursor ?? undefined;
      } while (cursor);
      return list;
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

/** Accept a rule suggestion (or a rejected one after all), or reject one; owners and editors. */
export function useDecideRuleSuggestion(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ key, action }: { key: string; action: "accept" | "reject" }) => {
      const params = { path: { workspace_id: workspaceId } };
      const { error, response } =
        action === "accept"
          ? await client.POST("/api/v1/workspaces/{workspace_id}/kpis/suggestions/accept", {
              params,
              body: { key },
            })
          : await client.POST("/api/v1/workspaces/{workspace_id}/kpis/suggestions/dismiss", {
              params,
              body: { key },
            });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
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
