// Admin: web search and web fetch for the assistant (ADR 0008).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type WebSettings = components["schemas"]["WebSettingsOut"];
export type WebSettingsInput = components["schemas"]["WebSettingsIn"];
export type WebProvider = components["schemas"]["WebProviderOut"];
export type WebProviderInput = components["schemas"]["WebProviderIn"];
export type WebProviderKind = WebProvider["kind"];

const webKey = ["admin", "web"] as const;

export function useWebSettings() {
  const client = useApiClient();
  return useQuery({
    queryKey: webKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/admin/web");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useSaveWebSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: WebSettingsInput) => {
      const { data, error, response } = await client.PUT("/api/v1/admin/web", {
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (settings) => queryClient.setQueryData(webKey, settings),
  });
}

export function useSaveWebProvider() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      kind,
      body,
    }: {
      kind: WebProviderKind;
      body: WebProviderInput;
    }) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/web/providers/{kind}",
        { params: { path: { kind } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (settings) => queryClient.setQueryData(webKey, settings),
  });
}

export function useDeleteWebProvider() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (kind: WebProviderKind) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/web/providers/{kind}",
        { params: { path: { kind } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: webKey }),
  });
}

export function useTestWebProvider() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (kind: WebProviderKind) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/web/providers/{kind}/test",
        { params: { path: { kind } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: webKey }),
  });
}
