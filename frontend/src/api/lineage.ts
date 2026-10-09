// The lineage graph and impact report (spec §6.14, stories 79, 110, 111, 112).
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type NodeLineage = components["schemas"]["NodeLineage"];
export type LineageNode = components["schemas"]["LineageNode"];
export type LineageEdge = components["schemas"]["LineageGraphEdge"];
export type ImpactReport = components["schemas"]["ImpactReport"];
export type LineageDirection = "upstream" | "downstream" | "both";

export const LAYER_LABELS: Record<LineageNode["layer"], string> = {
  source: "Source",
  staging: "Staging",
  core: "Core",
  mart: "Mart",
  kpi: "KPI",
};

export const EDGE_KIND_LABELS: Record<LineageEdge["kind"], string> = {
  value: "Feeds the value",
  uses: "Join, filter or GROUP BY",
  lookup: "Dimension lookup",
  kpi: "Linked by the KPI",
};

export interface LineageRequest {
  node: string;
  direction: LineageDirection;
  /** At most this many edges away; leave out for the whole lineage. */
  depth?: number;
}

/** A function that fetches a node's lineage (for expanding the graph on demand). */
export function useFetchLineage(workspaceId: string) {
  const client = useApiClient();
  return async ({ node, direction, depth }: LineageRequest): Promise<NodeLineage> => {
    const { data, error, response } = await client.GET(
      "/api/v1/workspaces/{workspace_id}/data-warehouse/lineage",
      {
        params: {
          path: { workspace_id: workspaceId },
          query: { node, direction, ...(depth ? { depth } : {}) },
        },
      },
    );
    if (error) {
      throw new ApiError(response.status, error.error);
    }
    return data;
  };
}

/** The lineage around a node; `null` fetches nothing. */
export function useLineage(workspaceId: string, request: LineageRequest | null) {
  const fetchLineage = useFetchLineage(workspaceId);
  return useQuery({
    queryKey: ["workspace", workspaceId, "lineage", request],
    enabled: request !== null,
    queryFn: () => fetchLineage(request as LineageRequest),
  });
}

/** The DW columns, DW tables and KPIs downstream of a node; `null` fetches nothing. */
export function useImpact(workspaceId: string, node: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", workspaceId, "lineage-impact", node],
    enabled: node !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/impact",
        { params: { path: { workspace_id: workspaceId }, query: { node: node as string } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
