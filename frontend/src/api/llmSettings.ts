// Admin: monthly token budgets and AI usage.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type LlmBudgets = components["schemas"]["LlmBudgets"];
export type LlmUsage = components["schemas"]["LlmUsage"];

const budgetsKey = ["admin", "llm", "budgets"] as const;
const usageKey = ["admin", "llm", "usage"] as const;

export function useLlmBudgets() {
  const client = useApiClient();
  return useQuery({
    queryKey: budgetsKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/budgets",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

function useBudgetRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: budgetsKey });
    void queryClient.invalidateQueries({ queryKey: usageKey });
  };
}

/** `null` removes the installation's limit. */
export function useSetInstallationBudget() {
  const client = useApiClient();
  const refresh = useBudgetRefresh();
  return useMutation({
    mutationFn: async (monthly_token_budget: number | null) => {
      const { error, response } = await client.PUT(
        "/api/v1/admin/llm/budgets/installation",
        {
          body: { monthly_token_budget },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export function useSetWorkspaceBudget() {
  const client = useApiClient();
  const refresh = useBudgetRefresh();
  return useMutation({
    mutationFn: async (input: { workspaceId: string; tokens: number }) => {
      const { error, response } = await client.PUT(
        "/api/v1/admin/llm/budgets/workspaces/{workspace_id}",
        {
          params: { path: { workspace_id: input.workspaceId } },
          body: { monthly_token_budget: input.tokens },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export function useClearWorkspaceBudget() {
  const client = useApiClient();
  const refresh = useBudgetRefresh();
  return useMutation({
    mutationFn: async (workspaceId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/llm/budgets/workspaces/{workspace_id}",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export type LlmModelPrice = components["schemas"]["LlmModelPrice"];
export type LlmCost = components["schemas"]["LlmCost"];

const pricesKey = ["admin", "llm", "prices"] as const;

export function useLlmModelPrices() {
  const client = useApiClient();
  return useQuery({
    queryKey: pricesKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/prices",
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

function usePriceRefresh() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: pricesKey });
    void queryClient.invalidateQueries({ queryKey: usageKey });
  };
}

export function useSetLlmModelPrice() {
  const client = useApiClient();
  const refresh = usePriceRefresh();
  return useMutation({
    mutationFn: async (body: LlmModelPrice) => {
      const { error, response } = await client.PUT("/api/v1/admin/llm/prices", {
        body,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

export function useClearLlmModelPrice() {
  const client = useApiClient();
  const refresh = usePriceRefresh();
  return useMutation({
    mutationFn: async (modelName: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/llm/prices",
        { params: { query: { model_name: modelName } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: refresh,
  });
}

/** What calls cost: per currency, with unknown for calls on a model without a price. */
export function formatCost(cost: LlmCost): string {
  const known = cost.amounts
    .map(
      (a) =>
        `${Number(a.amount).toLocaleString("en", { maximumFractionDigits: 4 })} ${a.currency}`,
    )
    .join(" + ");
  if (cost.unpriced_calls === 0) return known || "0";
  return known ? `${known} + unknown` : "Unknown";
}

/** Usage in a `YYYY-MM` month; `undefined`: this month. */
export function useLlmUsage(month: string | undefined) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...usageKey, month ?? "current"],
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/admin/llm/usage",
        {
          params: { query: month ? { month } : {} },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}
