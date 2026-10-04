// Workspace membership: the member list, adding or inviting people, roles, removing,
// leaving and transferring ownership (spec stories 30-34).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";
import type { WorkspaceRole } from "./workspaces";

export type Member = components["schemas"]["Member"];

const membersKey = (workspaceId: string) => ["workspace", workspaceId, "members"] as const;

/** Every member of the Workspace, by display name. */
export function useMembers(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: membersKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/members",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** Add someone by email, or invite them when they have no account yet (`outcome`). */
export function useAddMember(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { email: string; role: WorkspaceRole }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/members",
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
  });
}

/** Change a member's role; refused with `last_owner` if no owner would be left. */
export function useChangeMemberRole(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: WorkspaceRole }) => {
      const { data, error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/members/{member_id}",
        { params: { path: { workspace_id: workspaceId, member_id: userId } }, body: { role } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
  });
}

export function useRemoveMember(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/workspaces/{workspace_id}/members/{member_id}",
        { params: { path: { workspace_id: workspaceId, member_id: userId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) }),
  });
}

/** Leave the Workspace; the caller decides where to go next. */
export function useLeaveWorkspace(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST("/api/v1/workspaces/{workspace_id}/leave", {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["workspace", workspaceId] });
      return queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });
}

/** Make another member an owner and step down to editor. */
export function useTransferOwnership(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/transfer-ownership",
        { params: { path: { workspace_id: workspaceId } }, body: { user_id: userId } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (workspace) => {
      queryClient.setQueryData(["workspace", workspaceId], workspace);
      void queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      return queryClient.invalidateQueries({ queryKey: membersKey(workspaceId) });
    },
  });
}
