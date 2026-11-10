// The signed-in user's notifications, read and unread, shown in the header.
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Notification = components["schemas"]["Notification"];
type NotificationPage = components["schemas"]["NotificationPage"];

const notificationsKey = ["notifications"] as const;
const REFRESH_MS = 60_000;

/** The notifications, newest first a page at a time, refreshed every minute. */
export function useNotifications(enabled = true) {
  const client = useApiClient();
  return useInfiniteQuery({
    queryKey: notificationsKey,
    enabled,
    refetchInterval: REFRESH_MS,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: NotificationPage) => last.next_cursor ?? undefined,
    queryFn: async ({ pageParam }): Promise<NotificationPage> => {
      const { data, error, response } = await client.GET("/api/v1/notifications", {
        params: { query: pageParam ? { cursor: pageParam } : {} },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useMarkNotificationRead() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error, response } = await client.POST(
        "/api/v1/notifications/{notification_id}/read",
        {
          params: { path: { notification_id: id } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey }),
  });
}

export function useMarkAllNotificationsRead() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST("/api/v1/notifications/read-all");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: notificationsKey }),
  });
}
