// Admin: user management and the security-event log.
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type AdminUser = components["schemas"]["AdminUser"];
export type AdminUserPage = components["schemas"]["AdminUserPage"];
export type CreateUserRequest = components["schemas"]["CreateUserRequest"];
export type UpdateUserRequest = components["schemas"]["UpdateUserRequest"];
export type SecurityEvent = components["schemas"]["SecurityEventOut"];
export type SecurityEventPage = components["schemas"]["SecurityEventPageOut"];

export interface UserFilters {
  q?: string;
  role?: "admin" | "user";
  active?: boolean;
}

export interface SecurityEventFilters {
  event_type?: string;
  actor?: string;
  since?: string;
  until?: string;
}

const usersKey = ["admin", "users"] as const;
const eventsKey = ["admin", "security-events"] as const;

/** Users by email, a page at a time (`fetchNextPage` loads the next one). */
export function useUsers(filters: UserFilters) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: [...usersKey, filters],
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: AdminUserPage) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    queryFn: async ({ pageParam }): Promise<AdminUserPage> => {
      const { data, error, response } = await client.GET("/api/v1/admin/users", {
        params: { query: { ...filters, cursor: pageParam } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useCreateUser() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: CreateUserRequest) => {
      const { data, error, response } = await client.POST("/api/v1/admin/users", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: usersKey }),
  });
}

export function useUpdateUser() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: UpdateUserRequest & { id: string }) => {
      const { data, error, response } = await client.PATCH("/api/v1/admin/users/{user_id}", {
        params: { path: { user_id: id } },
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: usersKey });
      // The change may have been to the admin's own role.
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

/** Force a password reset; resolves to how the link went out. */
export function useForcePasswordReset() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/users/{user_id}/force-reset",
        { params: { path: { user_id: id } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Security events, newest first, a page at a time. */
export function useSecurityEvents(filters: SecurityEventFilters) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: [...eventsKey, filters],
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: SecurityEventPage) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    queryFn: async ({ pageParam }): Promise<SecurityEventPage> => {
      const { data, error, response } = await client.GET("/api/v1/admin/security-events", {
        params: { query: { ...filters, cursor: pageParam } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
