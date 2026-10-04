import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Workspace = components["schemas"]["Workspace"];
export type WorkspaceRole = Workspace["role"];
export type WorkspaceAction = components["schemas"]["Action"];
export type CreateWorkspaceRequest = components["schemas"]["CreateWorkspaceRequest"];
export type UpdateWorkspaceRequest = components["schemas"]["UpdateWorkspaceRequest"];

export const ROLE_LABELS: Record<WorkspaceRole, string> = {
  owner: "Owner",
  editor: "Editor",
  viewer: "Viewer",
};

/** Whether the API allows `action` for the signed-in user (the server checks again). */
export function allows(workspace: Workspace, action: WorkspaceAction): boolean {
  return workspace.permissions.includes(action);
}

const listKey = ["workspaces"] as const;
const workspaceKey = (id: string) => ["workspace", id] as const;

// An answer such as 404 will not change on retry; a 5xx or a network error might.
function retryUnlessRefused(failures: number, error: Error): boolean {
  return !(error instanceof ApiError && error.status < 500) && failures < 3;
}

/** The Workspaces the signed-in user belongs to, a page at a time. */
export function useWorkspaces() {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: listKey,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const { data, error, response } = await client.GET("/api/v1/workspaces", {
        params: { query: pageParam ? { cursor: pageParam } : {} },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

/** One Workspace; fails with an `ApiError` (404) when it is missing or not the user's. */
export function useWorkspace(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: workspaceKey(id),
    retry: retryUnlessRefused,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/workspaces/{workspace_id}", {
        params: { path: { workspace_id: id } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useCreateWorkspace() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateWorkspaceRequest) => {
      const { data, error, response } = await client.POST("/api/v1/workspaces", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (workspace) => {
      queryClient.setQueryData(workspaceKey(workspace.id), workspace);
      return queryClient.invalidateQueries({ queryKey: listKey });
    },
  });
}

/** Edit a Workspace's details; a stale `version` fails with `ApiError` 409. */
export function useUpdateWorkspace(id: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: UpdateWorkspaceRequest) => {
      const { data, error, response } = await client.PATCH("/api/v1/workspaces/{workspace_id}", {
        params: { path: { workspace_id: id } },
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (workspace) => {
      queryClient.setQueryData(workspaceKey(id), workspace);
      return queryClient.invalidateQueries({ queryKey: listKey });
    },
  });
}

export type StageProgress = components["schemas"]["StageProgress"];
export type StageStatus = StageProgress["kpis"]["status"];

/** Where the Workspace's work stands; every member sees the same. */
export function useStageProgress(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", id, "progress"] as const,
    retry: retryUnlessRefused,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/progress",
        { params: { path: { workspace_id: id } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
