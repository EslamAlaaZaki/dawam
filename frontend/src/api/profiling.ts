// Profiling of Source Tables (spec stories 55-57).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { jobsKey } from "./jobs";
import type { components } from "./schema";

export type TableProfile = components["schemas"]["TableProfile"];
export type ColumnProfileView = components["schemas"]["ColumnProfileView"];
export type ImportStatus = components["schemas"]["ImportStatus"];

const DEFAULT_ROW_CAP = 100_000;
const DEFAULT_TIMEOUT_SECONDS = 30;

export const tableProfileKey = (
  workspaceId: string,
  systemId: string,
  tableId: string,
) =>
  [
    "workspace",
    workspaceId,
    "systems",
    systemId,
    "tables",
    tableId,
    "profile",
  ] as const;

/** A table's profile with its columns'. */
export function useTableProfile(
  workspaceId: string,
  systemId: string,
  tableId: string,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: tableProfileKey(workspaceId, systemId, tableId),
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profile",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              table_id: tableId,
            },
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

/** Whether the system was built from a Schema Import (so profiling is unavailable). */
export function useImportStatus(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: [
      "workspace",
      workspaceId,
      "systems",
      systemId,
      "import-status",
    ] as const,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Start a profiling job for a table; 409 `profiling_unavailable` without a Connection. */
export function useStartProfiling(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (tableId: string) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/profiling",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: {
            table_ids: [tableId],
            row_cap: DEFAULT_ROW_CAP,
            timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
          },
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

/** An owner's per-table top-N switch. */
export function useSetTopN(
  workspaceId: string,
  systemId: string,
  tableId: string,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profiling-settings",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              table_id: tableId,
            },
          },
          body: { enabled },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) =>
      queryClient.setQueryData(
        tableProfileKey(workspaceId, systemId, tableId),
        data,
      ),
  });
}
