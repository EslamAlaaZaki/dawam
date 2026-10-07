// Change Sets: proposed changes the user reviews as a diff (spec stories 147, 148).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type ChangeSet = components["schemas"]["ChangeSet"];
export type ChangeSetItem = components["schemas"]["ChangeSetItem"];
export type ChangeSetDetail = components["schemas"]["ChangeSetDetail"];
export type ApplyResult = components["schemas"]["ApplyResult"];
export type ItemStatus = ChangeSetItem["status"];

export const ITEM_STATUS_LABELS: Record<ItemStatus, string> = {
  pending: "Pending",
  needs_owner: "Needs an owner",
  accepted: "Accepted",
  rejected: "Rejected",
  stale: "Stale",
  expired: "Expired",
};

const changeSetsKey = (workspaceId: string) =>
  ["workspace", workspaceId, "change-sets"] as const;

/** The Change Sets the assistant proposed in one conversation, newest first. */
export function useConversationChangeSets(
  workspaceId: string,
  conversationId: string | null,
  messageCount = 0,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [
      ...changeSetsKey(workspaceId),
      "conversation",
      conversationId,
      messageCount,
    ],
    enabled: conversationId !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/change-sets",
        {
          params: {
            path: { workspace_id: workspaceId },
            query: { conversation_id: conversationId ?? undefined, limit: 20 },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** One Change Set with its items. */
export function useChangeSet(workspaceId: string, changeSetId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...changeSetsKey(workspaceId), changeSetId],
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/change-sets/{change_set_id}",
        {
          params: {
            path: { workspace_id: workspaceId, change_set_id: changeSetId },
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

/** Accept the given items (all open ones when `itemIds` is omitted); fails with 409
 * `change_set_closed` if someone decided first. */
export function useAcceptItems(workspaceId: string, changeSetId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemIds?: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/change-sets/{change_set_id}/accept",
        {
          params: {
            path: { workspace_id: workspaceId, change_set_id: changeSetId },
          },
          body: { item_ids: itemIds ?? null },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    // The applied changes touch other screens (the Source Schema), so refresh everything
    // of the Workspace.
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] }),
  });
}

/** Reject the given items (all open ones when `itemIds` is omitted). */
export function useRejectItems(workspaceId: string, changeSetId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (itemIds?: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/change-sets/{change_set_id}/reject",
        {
          params: {
            path: { workspace_id: workspaceId, change_set_id: changeSetId },
          },
          body: { item_ids: itemIds ?? null },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: changeSetsKey(workspaceId) }),
  });
}
