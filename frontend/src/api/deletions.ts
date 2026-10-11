// Soft deletes of Source Systems and DW tables, the "deleted" list and permanent deletes
// (spec §7 "Soft deletes").
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type DeletedObjects = components["schemas"]["DeletedObjects"];
export type SourceSystemDeletion = components["schemas"]["SourceSystemDeletion"];
export type DwTableDeletion = components["schemas"]["DwTableDeletion"];

const deletedKey = (workspaceId: string) => ["workspace", workspaceId, "deleted"] as const;

/** A delete changes the model, the systems and the deleted list: refetch the Workspace's data. */
function useRefreshWorkspace(workspaceId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] });
}

/** The soft-deleted Source Systems and DW tables. */
export function useDeletedObjects(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: deletedKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/deleted",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** What purging a deleted Source System takes with it (owners only). */
export function useSystemDeletion(workspaceId: string, systemId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...deletedKey(workspaceId), "systems", systemId],
    enabled: systemId !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/deleted/systems/{system_id}",
        { params: { path: { workspace_id: workspaceId, system_id: systemId as string } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** What purging a deleted DW table takes with it (owners only). */
export function useTableDeletion(workspaceId: string, tableId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...deletedKey(workspaceId), "tables", tableId],
    enabled: tableId !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/deleted/tables/{dw_table_id}",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId as string } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Soft-delete a Source System (owners only); it moves to the deleted list. */
export function useDeleteSourceSystem(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshWorkspace(workspaceId);
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

/** Delete a deleted Source System for good (owners only). */
export function usePurgeSourceSystem(workspaceId: string) {
  const client = useApiClient();
  const refresh = useRefreshWorkspace(workspaceId);
  return useMutation({
    mutationFn: async (systemId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/deleted/systems/{system_id}",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

/** Delete a deleted DW table for good (owners only). */
export function usePurgeDwTable(workspaceId: string) {
  const client = useApiClient();
  const refresh = useRefreshWorkspace(workspaceId);
  return useMutation({
    mutationFn: async (tableId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/deleted/tables/{dw_table_id}",
        { params: { path: { workspace_id: workspaceId, dw_table_id: tableId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}
