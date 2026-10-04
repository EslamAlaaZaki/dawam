// Invitations: admins invite by email and revoke; the invitee looks up and accepts.
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { meQueryKey } from "./queries";
import type { components } from "./schema";

export type PendingInvitation = components["schemas"]["PendingInvitation"];
export type PendingInvitationPage = components["schemas"]["PendingInvitationPage"];
export type AcceptInvitationRequest = components["schemas"]["AcceptInvitationRequest"];

const invitationsKey = ["admin", "invitations"] as const;

/** Pending invitations by email, a page at a time (`fetchNextPage` loads the next one). */
export function usePendingInvitations() {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: invitationsKey,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: PendingInvitationPage) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    queryFn: async ({ pageParam }): Promise<PendingInvitationPage> => {
      const { data, error, response } = await client.GET("/api/v1/admin/invitations", {
        params: { query: { cursor: pageParam } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Invite someone by email; resolves to the invitation and how its link went out. */
export function useInviteUser() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (email: string) => {
      const { data, error, response } = await client.POST("/api/v1/admin/users/invite", {
        body: { email },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invitationsKey }),
  });
}

export function useRevokeInvitation() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await client.DELETE("/api/v1/admin/invitations/{invitation_id}", {
        params: { path: { invitation_id: id } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: invitationsKey }),
  });
}

/** Who an invitation link is for; fails with `invalid_invitation` once it cannot be used. */
export function useInvitationLink(token: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["invitation", token],
    enabled: Boolean(token),
    queryFn: async () => {
      const { data, error, response } = await client.POST("/api/v1/auth/invitations/lookup", {
        body: { token: token ?? "" },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Accept an invitation; on success the new user is signed in. */
export function useAcceptInvitation() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AcceptInvitationRequest) => {
      const { data, error, response } = await client.POST("/api/v1/auth/invitations/accept", {
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (me) => {
      queryClient.setQueryData(meQueryKey, me);
    },
  });
}
