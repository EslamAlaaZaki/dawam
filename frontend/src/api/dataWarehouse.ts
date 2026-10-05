import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type DataWarehouse = components["schemas"]["DataWarehouse"];
export type SetUpDataWarehouseRequest = components["schemas"]["SetUpRequest"];
export type UpdateDataWarehouseRequest = components["schemas"]["UpdateRequest"];
export type TargetPlatform = components["schemas"]["Platform"]["platform"];

export const PLATFORM_LABELS: Record<TargetPlatform, string> = {
  postgresql: "PostgreSQL",
  sqlserver: "SQL Server",
  oracle: "Oracle",
  snowflake: "Snowflake",
  bigquery: "BigQuery",
};

const key = (workspaceId: string) => ["workspace", workspaceId, "data-warehouse"] as const;

/** The Data Warehouse's setup; `set_up` is false until the step is done. */
export function useDataWarehouse(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: key(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Set the Data Warehouse up (editors and owners). */
export function useSetUpDataWarehouse(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SetUpDataWarehouseRequest) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (warehouse) => {
      queryClient.setQueryData(key(workspaceId), warehouse);
    },
  });
}

/** Change the setup; a stale `version` fails with 409, a platform change by a non-owner 403. */
export function useUpdateDataWarehouse(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: UpdateDataWarehouseRequest) => {
      const { data, error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/data-warehouse",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (warehouse) => {
      queryClient.setQueryData(key(workspaceId), warehouse);
    },
  });
}
