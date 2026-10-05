// A Source System's live database Connection (spec stories 40-43). Owners only.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Connection = components["schemas"]["Connection"];
export type ConnectionRequest = components["schemas"]["ConnectionRequest"];
export type ConnectionTestResult = components["schemas"]["ConnectionTestResult"];

const PATH = "/api/v1/workspaces/{workspace_id}/systems/{system_id}/connection" as const;
const TEST_PATH = "/api/v1/workspaces/{workspace_id}/systems/{system_id}/connection/test" as const;

const connectionKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "connection"] as const;

/** The system's Connection, or `null` while it has none. */
export function useConnection(workspaceId: string, systemId: string, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: connectionKey(workspaceId, systemId),
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
      });
      if (error) {
        if (response.status === 404 && error.error.code === "connection_not_found") {
          return null;
        }
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Create or replace the Connection; an omitted `password` keeps the stored one. */
export function useSaveConnection(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConnectionRequest) => {
      const { data, error, response } = await client.PUT(PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) => queryClient.setQueryData(connectionKey(workspaceId, systemId), data),
  });
}

/** Try the settings without saving them; a failed test is a result (`ok: false`), not an error. */
export function useTestConnection(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConnectionRequest) => {
      const { data, error, response } = await client.POST(TEST_PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    // Testing the saved settings records the result on the Connection.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: connectionKey(workspaceId, systemId) }),
  });
}
