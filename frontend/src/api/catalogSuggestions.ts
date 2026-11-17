// AI data catalog suggestions of one Source System (descriptions, classifications, SCD hints)
// and the manual edits of the same fields. A suggestion is its own record: it changes nothing
// until a person accepts it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";
import { sourceSchemaKey } from "./snapshots";

export type CatalogSuggestion = components["schemas"]["CatalogSuggestion"];
export type CatalogSuggestionJob = components["schemas"]["CatalogSuggestionJob"];
export type CatalogSuggestionCounts = components["schemas"]["CatalogSuggestionCounts"];
export type CatalogField = CatalogSuggestion["field"];
export type CatalogStatus = "pending" | "accepted" | "rejected";
export type TableClassification = NonNullable<
  components["schemas"]["UpdateTableEnhancements"]["classification"]
>;

export const CLASSIFICATIONS: readonly TableClassification[] = [
  "master",
  "transactional",
  "reference",
  "log",
  "landing",
];

export const FIELD_LABELS: Record<CatalogField, string> = {
  description: "Description",
  classification: "Classification",
  scd_hint: "SCD hint",
};

const ACTIVE: ReadonlySet<CatalogSuggestionJob["status"]> = new Set([
  "queued",
  "running",
  "paused",
]);
const REFRESH_MS = 3_000;

/** Whether the run has not ended yet. */
export function isRunning(job: CatalogSuggestionJob | null | undefined): boolean {
  return job != null && ACTIVE.has(job.status);
}

export const catalogKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "catalog-suggestions"] as const;

/** The newest run (owners and editors); refreshed while it runs. */
export function useCatalogSuggestionJob(
  workspaceId: string,
  systemId: string,
  enabled: boolean,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: [...catalogKey(workspaceId, systemId), "job"] as const,
    enabled,
    retry: false,
    refetchInterval: (query) => (isRunning(query.state.data) ? REFRESH_MS : false),
    queryFn: async (): Promise<CatalogSuggestionJob | null> => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.job ?? null;
    },
  });
  // When a run ends, its suggestions (and the pending count on the tab) load again.
  const running = isRunning(job.data);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (wasRunning.current && !running) {
      void queryClient.invalidateQueries({
        queryKey: catalogKey(workspaceId, systemId),
        predicate: (query) => query.queryKey.at(-1) !== "job",
      });
    }
    wasRunning.current = running;
  }, [running, queryClient, workspaceId, systemId]);
  return job;
}

/** Start a run; fails with `catalog_suggestions_running` or `no_snapshot` (409). */
export function useStartCatalogSuggestions(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const key = catalogKey(workspaceId, systemId);
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions",
        { params: { path: { workspace_id: workspaceId, system_id: systemId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (job) =>
      queryClient.setQueryData<CatalogSuggestionJob | null>([...key, "job"], job),
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });
}

/**
 * The suggestions in one state, of one table when `tableId` is given, with the counts of
 * every state. Only asked for once `enabled` (owners and editors).
 */
export function useCatalogSuggestions(
  workspaceId: string,
  systemId: string,
  status: CatalogStatus,
  tableId: string | null,
  enabled: boolean,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...catalogKey(workspaceId, systemId), "items", status, tableId] as const,
    enabled,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions/items",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { status, ...(tableId ? { table_id: tableId } : {}) },
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

/** A decision changes the saved values, so the Source Schema reloads with it. */
function useRefreshAfter(workspaceId: string, systemId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: catalogKey(workspaceId, systemId) });
    void queryClient.invalidateQueries({ queryKey: sourceSchemaKey(workspaceId, systemId) });
  };
}

/** Accept the suggestions as the AI wrote them; ones no longer pending come back in `skipped`. */
export function useAcceptSuggestions(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshAfter(workspaceId, systemId);
  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions/accept",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { ids },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: refresh,
  });
}

/** Accept every pending suggestion, or those of one table. */
export function useAcceptAllSuggestions(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshAfter(workspaceId, systemId);
  return useMutation({
    mutationFn: async (tableId: string | null) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions/accept-all",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { table_id: tableId },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: refresh,
  });
}

/** Reject the suggestions; a rejected value is not suggested again for that field. */
export function useRejectSuggestions(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshAfter(workspaceId, systemId);
  return useMutation({
    mutationFn: async (ids: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions/reject",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { ids },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: refresh,
  });
}

/** Accept one suggestion with the person's own value (no AI tag after). */
export function useAcceptEditedSuggestion(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const refresh = useRefreshAfter(workspaceId, systemId);
  return useMutation({
    mutationFn: async ({ id, value }: { id: string; value: string }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/catalog-suggestions/accept-edited",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { id, value },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: refresh,
  });
}

export type CatalogTarget =
  | { kind: "table"; tableId: string; version: number }
  | { kind: "column"; tableId: string; columnId: string; version: number };

/** Edit one catalog field by hand: a table's description, classification or SCD hint, or a column's description. */
export function useEditCatalogField(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      target,
      field,
      value,
    }: {
      target: CatalogTarget;
      field: CatalogField;
      value: string | null;
    }) => {
      const path = {
        workspace_id: workspaceId,
        system_id: systemId,
        table_id: target.tableId,
      };
      if (target.kind === "column") {
        const { error, response } = await client.PATCH(
          "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/columns/{column_id}",
          {
            params: { path: { ...path, column_id: target.columnId } },
            body: { version: target.version, description: value },
          },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        return;
      }
      const { error, response } = await client.PATCH(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}",
        {
          params: { path },
          body: {
            version: target.version,
            ...(field === "classification"
              ? { classification: value as TableClassification | null }
              : field === "scd_hint"
                ? { scd_hint: value }
                : { description: value }),
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: sourceSchemaKey(workspaceId, systemId) });
      void queryClient.invalidateQueries({ queryKey: catalogKey(workspaceId, systemId) });
    },
  });
}
