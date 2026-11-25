// Column mappings and lineage (spec §6.14, stories 99, 100, 104).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type TableMapping = components["schemas"]["TableMapping"];
export type ColumnMapping = components["schemas"]["ColumnMapping"];
export type MappingType = ColumnMapping["mapping_type"];
export type SaveColumnMappingRequest = components["schemas"]["SaveColumnMappingRequest"];

export const MAPPING_TYPE_LABELS: Record<MappingType, string> = {
  direct: "Direct",
  derived: "Derived",
  constant: "Constant",
  lookup: "Dimension lookup",
  system: "System-generated",
  unmapped: "Unmapped",
  not_in_branch: "Not in this branch (NULL)",
};

/** The types a table-level mapping offers; `not_in_branch` only applies inside a branch. */
export const TABLE_LEVEL_MAPPING_TYPES: MappingType[] = [
  "direct",
  "derived",
  "constant",
  "unmapped",
];

export type StagingMappingList = components["schemas"]["StagingMappingList"];
export type StagingMappingTable = components["schemas"]["StagingMappingListTable"];
export type StagingMappingColumn = components["schemas"]["StagingMappingListColumn"];

/** The Staging Layer's mappings: per Staging Table, each column's source column. */
export function useStagingMappings(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", workspaceId, "dw-staging-mappings"] as const,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/staging/mappings",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Where a member downloads the Staging Layer's mapping sheet (any member). */
export function stagingMappingSheetUrl(workspaceId: string, format: "xlsx" | "csv"): string {
  return `/api/v1/workspaces/${workspaceId}/data-warehouse/mapping-sheet?layer=staging&format=${format}`;
}

/** Where a member downloads a Core or Mart Layer's mapping sheet (any member). */
export function layerMappingSheetUrl(
  workspaceId: string,
  layer: "core" | "mart",
  format: "xlsx" | "csv",
): string {
  return `/api/v1/workspaces/${workspaceId}/data-warehouse/mapping-sheet?layer=${layer}&format=${format}`;
}

export type CoverageReport = components["schemas"]["CoverageReport"];
export type MappingBranch = components["schemas"]["MappingBranch"];

/** Mapping coverage per Core and Mart table, per Layer and overall. */
export function useCoverage(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", workspaceId, "dw-coverage"] as const,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/coverage",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

const key = (workspaceId: string, tableId: string) =>
  ["workspace", workspaceId, "dw-mapping", tableId] as const;

/** A Core or Mart table's mapping from the Layer below, one entry per column. */
export function useTableMapping(workspaceId: string, tableId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: key(workspaceId, tableId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/mapping",
        {
          params: { path: { workspace_id: workspaceId, dw_table_id: tableId } },
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
 * Save one column's mapping; fails with `invalid_mapping` (422) or `version_conflict`
 * (409). SQL that does not parse is saved, flagged in `validation`.
 */
export function useSaveColumnMapping(workspaceId: string, tableId: string, columnId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SaveColumnMappingRequest) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/mapping/columns/{dw_column_id}",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              dw_table_id: tableId,
              dw_column_id: columnId,
            },
          },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => invalidateMapping(queryClient, workspaceId, tableId),
  });
}

function invalidateMapping(
  queryClient: ReturnType<typeof useQueryClient>,
  workspaceId: string,
  tableId: string,
) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: key(workspaceId, tableId) }),
    queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId, "dw-coverage"] }),
  ]);
}

/** Save one column's mapping within a branch; the same errors as the table-level save. */
export function useSaveBranchColumnMapping(
  workspaceId: string,
  tableId: string,
  branchId: string,
  columnId: string,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SaveColumnMappingRequest) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/mapping/branches/{branch_id}/columns/{dw_column_id}",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              dw_table_id: tableId,
              branch_id: branchId,
              dw_column_id: columnId,
            },
          },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => invalidateMapping(queryClient, workspaceId, tableId),
  });
}
