// Admin: every Workspace's metadata (never its content) and ownership reassignment.
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type AdminWorkspace = components["schemas"]["AdminWorkspace"];
export type AdminWorkspacePage = components["schemas"]["AdminWorkspacePage"];

const key = ["admin", "workspaces"] as const;

/** Every Workspace by name, a page at a time. */
export function useAdminWorkspaces() {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: key,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: AdminWorkspacePage) => last.next_cursor ?? undefined,
    queryFn: async ({ pageParam }): Promise<AdminWorkspacePage> => {
      const { data, error, response } = await client.GET("/api/v1/admin/workspaces", {
        params: { query: pageParam ? { cursor: pageParam } : {} },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Make an active user an owner of a Workspace whose owners are all deactivated. */
export function useReassignOwner() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ workspaceId, userId }: { workspaceId: string; userId: string }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/workspaces/{workspace_id}/reassign-owner",
        { params: { path: { workspace_id: workspaceId } }, body: { user_id: userId } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
}
