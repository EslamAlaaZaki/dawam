// Admin: LLM providers, their models and the AI setup status.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type LlmProvider = components["schemas"]["LlmProvider"];
export type LlmModel = components["schemas"]["LlmModel"];
export type LlmProviderInput = components["schemas"]["LlmProviderRequest"];
export type LlmModelInput = components["schemas"]["LlmModelRequest"];
export type LlmModelUpdate = components["schemas"]["LlmModelUpdateRequest"];
export type LlmModelRole = LlmModel["roles"][number];
export type LlmModelUsage = components["schemas"]["LlmModelUsage"];
export type LlmModelRemoval = components["schemas"]["LlmModelRemoval"];
export type LlmSetup = components["schemas"]["LlmSetupStatus"];

const providersKey = ["admin", "llm", "providers"] as const;
const setupKey = ["admin", "llm", "setup"] as const;

export function useLlmProviders() {
  const client = useApiClient();
  return useQuery({
    queryKey: providersKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/providers",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** Whether AI is set up; `enabled: false` skips the request (e.g. for non-admins). */
export function useLlmSetup({ enabled = true }: { enabled?: boolean } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: setupKey,
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/setup",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

function useRefreshing() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: providersKey });
    void queryClient.invalidateQueries({ queryKey: setupKey });
  };
}

export function useCreateLlmProvider() {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (body: LlmProviderInput) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/llm/providers",
        {
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

export function useUpdateLlmProvider(providerId: string) {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (body: LlmProviderInput) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/llm/providers/{provider_id}",
        { params: { path: { provider_id: providerId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

export function useDeleteLlmProvider() {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (providerId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/llm/providers/{provider_id}",
        { params: { path: { provider_id: providerId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export function useAddLlmModel(providerId: string) {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (body: LlmModelInput) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/llm/providers/{provider_id}/models",
        { params: { path: { provider_id: providerId } }, body },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

export function useUpdateLlmModel(modelId: string) {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (body: LlmModelUpdate) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/llm/models/{model_id}",
        {
          params: { path: { model_id: modelId } },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Set the roles a model fills (any combination). Ticking embedding runs a test. */
export function useSetLlmModelRoles(modelId: string) {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (roles: LlmModelRole[]) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/admin/llm/models/{model_id}/roles",
        { params: { path: { model_id: modelId } }, body: { roles } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

export function useDeleteLlmModel() {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (modelId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/llm/models/{model_id}",
        {
          params: { path: { model_id: modelId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export function useTestLlmModel() {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (modelId: string) => {
      const { data, error, response } = await client.POST(
        "/api/v1/admin/llm/models/{model_id}/test",
        { params: { path: { model_id: modelId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: refresh,
  });
}

/** Where a model is used: what its removal popup has to ask a replacement for. */
export function useLlmModelUsage(
  modelId: string,
  { enabled }: { enabled: boolean },
) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["admin", "llm", "model-usage", modelId],
    enabled,
    gcTime: 0,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/models/{model_id}/usage",
        {
          params: { path: { model_id: modelId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Remove a model in use: one replacement per role, applied everywhere at once. */
export function useRemoveLlmModelWithReplacements(modelId: string) {
  const client = useApiClient();
  const refresh = useRefreshing();
  return useMutation({
    mutationFn: async (body: LlmModelRemoval) => {
      const { error, response } = await client.POST(
        "/api/v1/admin/llm/models/{model_id}/removal",
        {
          params: { path: { model_id: modelId } },
          body,
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}
