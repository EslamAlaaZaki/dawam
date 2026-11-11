// Profiling of Source Tables (spec stories 55-57).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { isActive, jobsKey, useJobs } from "./jobs";
import type { components } from "./schema";
import { sourceSchemaKey } from "./snapshots";

export type TableProfile = components["schemas"]["TableProfile"];
export type ColumnProfileView = components["schemas"]["ColumnProfileView"];
export type ImportStatus = components["schemas"]["ImportStatus"];
export type ProfilingOverview = components["schemas"]["ProfilingOverview"];
export type OverviewTable = components["schemas"]["OverviewTable"];
export type OverviewFinding = components["schemas"]["OverviewFinding"];
export type ProfilingGrowth = components["schemas"]["ProfilingGrowth"];
export type GrowthTable = components["schemas"]["GrowthTable"];

const DEFAULT_ROW_CAP = 100_000;
const DEFAULT_TIMEOUT_SECONDS = 30;

/** The prefix of every table profile of a Source System. */
export const tableProfilesKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "tables"] as const;

export const tableProfileKey = (
  workspaceId: string,
  systemId: string,
  tableId: string,
) => [...tableProfilesKey(workspaceId, systemId), tableId, "profile"] as const;

/** A table's profile with its columns'. */
export function useTableProfile(
  workspaceId: string,
  systemId: string,
  tableId: string,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: tableProfileKey(workspaceId, systemId, tableId),
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profile",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              table_id: tableId,
            },
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

/** The prefix of the Profiling page's overview and growth of a Source System. */
export const profilingPageKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "profiling"] as const;

/** Every table and view of the latest Snapshot with its profiling status and findings. */
export function useProfilingOverview(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...profilingPageKey(workspaceId, systemId), "overview"] as const,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/profiling/overview",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** How each table's row estimate grows across the Snapshots. */
export function useProfilingGrowth(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...profilingPageKey(workspaceId, systemId), "growth"] as const,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/profiling/growth",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Whether the system was built from a Schema Import (so profiling is unavailable). */
export function useImportStatus(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: [
      "workspace",
      workspaceId,
      "systems",
      systemId,
      "import-status",
    ] as const,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Start one profiling job for tables and views; 409 `profiling_unavailable` without a
 * Connection. */
export function useStartProfiling(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (tableIds: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/profiling",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: {
            table_ids: tableIds,
            row_cap: DEFAULT_ROW_CAP,
            timeout_seconds: DEFAULT_TIMEOUT_SECONDS,
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
  });
}

/** A profiling job started from the page, with the tables it profiles. */
export type ProfilingRun = { jobId: string; tableIds: string[] };

/**
 * The profiling jobs started from the page: which tables are being profiled now (a job
 * counts until the Workspace's jobs show it finished), and, as each job finishes, the
 * refreshed profiles of its tables, the Source Schema's profile dates and the Profiling
 * page.
 */
export function useProfilingRuns(workspaceId: string, systemId: string) {
  const [runs, setRuns] = useState<ProfilingRun[]>([]);
  const jobs = useJobs(workspaceId);
  const queryClient = useQueryClient();

  const byId = new Map(jobs.data?.items.map((job) => [job.id, job]));
  const done = runs.filter((run) => {
    const job = byId.get(run.jobId);
    return job != null && !isActive(job);
  });
  const doneKey = done.map((run) => run.jobId).join();
  useEffect(() => {
    if (doneKey === "") {
      return;
    }
    const finished = new Set(doneKey.split(","));
    for (const run of runs) {
      if (finished.has(run.jobId)) {
        for (const tableId of run.tableIds) {
          void queryClient.invalidateQueries({
            queryKey: tableProfileKey(workspaceId, systemId, tableId),
          });
        }
      }
    }
    void queryClient.invalidateQueries({
      queryKey: sourceSchemaKey(workspaceId, systemId),
    });
    void queryClient.invalidateQueries({
      queryKey: profilingPageKey(workspaceId, systemId),
    });
    // Only a newly finished job refreshes; `runs` changes with every start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneKey, queryClient, workspaceId, systemId]);

  const profiling = new Set(
    runs.filter((run) => !done.includes(run)).flatMap((run) => run.tableIds),
  );
  return {
    /** The tables a job started from the page is profiling now. */
    profiling,
    /** Follow a job that was just started. */
    follow: (run: ProfilingRun) => setRuns((current) => [...current, run]),
  };
}

/** An owner's per-table top-N switch. */
export function useSetTopN(
  workspaceId: string,
  systemId: string,
  tableId: string,
) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const { data, error, response } = await client.PUT(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/tables/{table_id}/profiling-settings",
        {
          params: {
            path: {
              workspace_id: workspaceId,
              system_id: systemId,
              table_id: tableId,
            },
          },
          body: { enabled },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (data) =>
      queryClient.setQueryData(
        tableProfileKey(workspaceId, systemId, tableId),
        data,
      ),
  });
}
