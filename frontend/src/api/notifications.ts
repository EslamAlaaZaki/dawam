// The signed-in user's unread notifications, shown in the header.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Notification = components["schemas"]["Notification"];

const notificationsKey = ["notifications"] as const;
const REFRESH_MS = 60_000;

/** The unread notifications, newest first, refreshed every minute. */
export function useNotifications(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: notificationsKey,
    enabled,
    refetchInterval: REFRESH_MS,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/notifications");
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
      const { error, response } = await client.POST("/api/v1/notifications/{notification_id}/read", {
        params: { path: { notification_id: id } },
      });
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
