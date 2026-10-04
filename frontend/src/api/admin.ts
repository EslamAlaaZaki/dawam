// TanStack Query hooks for the installation-wide settings (admins only).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type AdminSettings = components["schemas"]["AdminSettings"];
export type AdminSettingsUpdate = components["schemas"]["AdminSettingsUpdate"];

const adminSettingsKey = ["admin", "settings"] as const;

export function useAdminSettings({ enabled = true }: { enabled?: boolean } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: adminSettingsKey,
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/admin/settings");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useUpdateAdminSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: AdminSettingsUpdate) => {
      const { data, error, response } = await client.PUT("/api/v1/admin/settings", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (settings) => {
      queryClient.setQueryData(adminSettingsKey, settings);
      // Whether sign-up is open may have changed.
      void queryClient.invalidateQueries({ queryKey: ["registration"] });
    },
  });
}
