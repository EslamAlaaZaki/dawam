// The PII folder of a Source System: columns by tab, their review, and value scans
// (spec: PII review).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { jobsKey } from "./jobs";
import type { components } from "./schema";

const DEFAULT_SAMPLE_SIZE = 1000;

export type PiiColumn = components["schemas"]["PiiFolderColumn"];
export type PiiColumnPage = components["schemas"]["PiiColumnPage"];
export type PiiRuleMatch = components["schemas"]["PiiRuleMatch"];
export type PiiCategory = PiiRuleMatch["category"];
export type PiiTab = "pii" | "suggested" | "accepted" | "rejected";

export const PII_CATEGORIES: readonly PiiCategory[] = [
  "direct_identifier",
  "quasi_identifier",
  "sensitive",
  "financial",
];

export const CATEGORY_LABELS: Record<PiiCategory, string> = {
  direct_identifier: "Direct identifier",
  quasi_identifier: "Quasi-identifier",
  sensitive: "Sensitive",
  financial: "Financial",
};

const systemKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId] as const;

export const piiColumnsKey = (workspaceId: string, systemId: string) =>
  [...systemKey(workspaceId, systemId), "pii-columns"] as const;

/** One tab of the PII folder, with every tab's count. */
export function usePiiColumns(
  workspaceId: string,
  systemId: string,
  tab: PiiTab,
  enabled = true,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...piiColumnsKey(workspaceId, systemId), tab] as const,
    retry: false,
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { tab },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/**
 * Detect PII in the given tables: a value scan (only with a Connection; `job_id` is null
 * without one) and an AI detection (`ai_job_id`), two background jobs started together.
 */
export function useStartPiiScan(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (tableIds: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-scans",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { table_ids: tableIds, sample_size: DEFAULT_SAMPLE_SIZE },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
  });
}

/**
 * A PII decision changes the folder, the Source schema's flags, the Source summary's
 * counts and the stored profile values: everything of the system is read again.
 */
function useRefreshSystem(workspaceId: string, systemId: string) {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({ queryKey: systemKey(workspaceId, systemId) });
}

export type PiiDecision =
  | { columnId: string; action: "accept" | "accept-rejected"; category: PiiCategory }
  | { columnId: string; action: "reject" };

/** Accept or reject a suggested column, or accept a rejected one, with all its rules. */
export function useDecidePiiColumn(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshSystem(workspaceId, systemId);
  return useMutation({
    mutationFn: async (decision: PiiDecision) => {
      const path = {
        workspace_id: workspaceId,
        system_id: systemId,
        column_id: decision.columnId,
      };
      const { data, error, response } =
        decision.action === "reject"
          ? await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns/{column_id}/reject",
              { params: { path } },
            )
          : decision.action === "accept"
            ? await client.POST(
                "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns/{column_id}/accept",
                { params: { path }, body: { category: decision.category } },
              )
            : await client.POST(
                "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns/{column_id}/accept-rejected",
                { params: { path }, body: { category: decision.category } },
              );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Add a column as PII by hand, with a category. */
export function useAddPiiColumn(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshSystem(workspaceId, systemId);
  return useMutation({
    mutationFn: async (body: { column_id: string; category: PiiCategory }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Remove a column's PII; optionally profile its table again (a background job). */
export function useRemovePiiColumn(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const refresh = useRefreshSystem(workspaceId, systemId);
  return useMutation({
    mutationFn: async ({
      columnId,
      profileAgain,
    }: {
      columnId: string;
      profileAgain: boolean;
    }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-columns/{column_id}/remove",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              column_id: columnId,
            },
          },
          body: { profile_again: profileAgain },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      Promise.all([
        refresh(),
        queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
      ]),
  });
}
