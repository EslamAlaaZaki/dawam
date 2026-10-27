// A Workspace's background jobs (spec story 46): status, progress and log, and cancelling.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Job = components["schemas"]["Job"];
export type JobStatus = Job["status"];

const ACTIVE: ReadonlySet<JobStatus> = new Set(["queued", "running", "paused"]);
const REFRESH_MS = 3_000;

/** Whether the job is still waiting, running or paused for an answer (so it can be cancelled). */
export function isActive(job: Job): boolean {
  return ACTIVE.has(job.status);
}

export const jobsKey = (workspaceId: string) =>
  ["workspace", workspaceId, "jobs"] as const;

/** The Workspace's latest jobs, newest first; refreshed while any of them is active. */
export function useJobs(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: jobsKey(workspaceId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.items.some(isActive) ? REFRESH_MS : false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/jobs",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** One job (e.g. an assistant task started from the chat); refreshed while it is active. */
export function useJob(jobId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["job", jobId] as const,
    retry: false,
    refetchInterval: (query) =>
      query.state.data && !isActive(query.state.data) ? false : REFRESH_MS,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/jobs/{job_id}",
        {
          params: { path: { job_id: jobId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Answer the question a paused job asked; the job then resumes. */
export function useAnswerJob(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      jobId,
      answer,
    }: {
      jobId: string;
      answer: string;
    }) => {
      const { data, error, response } = await client.POST(
        "/api/v1/jobs/{job_id}/answer",
        {
          params: { path: { job_id: jobId } },
          body: { answer },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: (_data, _error, { jobId }) => {
      void queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      return queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) });
    },
  });
}

export function useCancelJob(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => {
      const { data, error, response } = await client.POST(
        "/api/v1/jobs/{job_id}/cancel",
        {
          params: { path: { job_id: jobId } },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: jobsKey(workspaceId) }),
  });
}
