// The Core and Mart model editor (spec stories 89-93a).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type DwTable = components["schemas"]["DwTable"];
export type DwTableSummary = components["schemas"]["DwTableSummary"];
export type DwColumn = components["schemas"]["DwColumn"];
export type DwLayer = DwTable["layer"];
export type CreateDwTableRequest = components["schemas"]["CreateDwTableRequest"];
export type UpdateDwTableRequest = components["schemas"]["UpdateDwTableRequest"];
export type CreateDwColumnRequest = components["schemas"]["CreateDwColumnRequest"];

export const FACT_TYPE_LABELS: Record<NonNullable<DwTable["fact_type"]>, string> = {
  transactional: "Transactional",
  periodic_snapshot: "Periodic snapshot",
  accumulating_snapshot: "Accumulating snapshot",
  factless: "Factless",
};

export const ADDITIVITY_LABELS: Record<NonNullable<DwColumn["additivity"]>, string> = {
  additive: "Additive",
  semi_additive: "Semi-additive",
  non_additive: "Non-additive",
};

/** What a user may give a column; the SCD housekeeping roles belong to DAWAM. */
export const COLUMN_ROLE_LABELS = {
  sk: "Surrogate key",
  nk: "Natural key",
  fk: "Foreign key",
  measure: "Measure",
  attribute: "Attribute",
  degenerate_dimension: "Degenerate dimension",
  audit: "Audit column",
} as const;

export const NEUTRAL_TYPES = [
  "smallint",
  "integer",
  "bigint",
  "decimal",
  "float",
  "double",
  "boolean",
  "char",
  "string",
  "text",
  "binary",
  "date",
  "time",
  "timestamp",
  "timestamptz",
  "uuid",
  "json",
] as const;

const root = (workspaceId: string) => ["workspace", workspaceId, "dw-model"] as const;
const listKey = (workspaceId: string, layer: DwLayer) =>
  [...root(workspaceId), "list", layer] as const;
const tableKey = (workspaceId: string, tableId: string) =>
  [...root(workspaceId), "table", tableId] as const;

/** The tables of one Layer, by name. */
export function useDwTables(workspaceId: string, layer: DwLayer) {
  const client = useApiClient();
  return useQuery({
    queryKey: listKey(workspaceId, layer),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables",
        { params: { path: { workspace_id: workspaceId }, query: { layer } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** One table with its columns. */
export function useDwTable(workspaceId: string, tableId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: tableKey(workspaceId, tableId ?? ""),
    enabled: tableId !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId as string } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Anything in the model may change what a table or the lists show: refetch them all. */
function useRefreshModel(workspaceId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: root(workspaceId) });
}

/** Create a Core or Mart table; fails with `invalid_model` (422) or `name_taken` (409). */
export function useCreateDwTable(workspaceId: string) {
  const client = useApiClient();
  const refresh = useRefreshModel(workspaceId);
  return useMutation({
    mutationFn: async (body: CreateDwTableRequest) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Edit a table; a stale `version` fails with `ApiError` 409. */
export function useUpdateDwTable(workspaceId: string, tableId: string) {
  const client = useApiClient();
  const refresh = useRefreshModel(workspaceId);
  return useMutation({
    mutationFn: async (body: UpdateDwTableRequest) => {
      const { data, error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Delete a table; fails with `table_referenced` (409) while another table points at it. */
export function useDeleteDwTable(workspaceId: string, tableId: string) {
  const client = useApiClient();
  const refresh = useRefreshModel(workspaceId);
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

/** Add a column to a table. */
export function useCreateDwColumn(workspaceId: string, tableId: string) {
  const client = useApiClient();
  const refresh = useRefreshModel(workspaceId);
  return useMutation({
    mutationFn: async (body: CreateDwColumnRequest) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/columns",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Delete a column (not an SCD2 housekeeping one). */
export function useDeleteDwColumn(workspaceId: string, tableId: string) {
  const client = useApiClient();
  const refresh = useRefreshModel(workspaceId);
  return useMutation({
    mutationFn: async (columnId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}/columns/{dw_column_id}",
        {
          params: {
            path: { workspace_id: workspaceId, dw_table_id: tableId, dw_column_id: columnId },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}
