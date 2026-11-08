// PII findings and value scans of a Source System (spec: PII review).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { jobsKey } from "./jobs";
import type { components } from "./schema";
import { sourceSummaryKey } from "./sourceSummary";

const DEFAULT_SAMPLE_SIZE = 1000;

export type PiiFinding = components["schemas"]["PiiFinding"];
export type PiiStatus = PiiFinding["status"];

export const piiFindingsKey = (workspaceId: string, systemId: string) =>
  ["workspace", workspaceId, "systems", systemId, "pii-findings"] as const;

/** The system's findings in one state, most confident first. */
export function usePiiFindings(
  workspaceId: string,
  systemId: string,
  status: PiiStatus,
) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...piiFindingsKey(workspaceId, systemId), status] as const,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings",
        {
          params: {
            path: { workspace_id: workspaceId, system_id: systemId },
            query: { status },
          },
        },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

/** Start a value scan of the given tables as a background job. */
export function useStartPiiScan(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (tableIds: string[]) => {
      const { data, error, response } = await client.POST(
        "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-scans",
        {
          params: { path: { workspace_id: workspaceId, system_id: systemId } },
          body: { table_ids: tableIds, sample_size: DEFAULT_SAMPLE_SIZE },
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

/** Confirm or dismiss a finding; the lists and the Source summary's count refresh. */
export function useDecidePiiFinding(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      id,
      decision,
    }: {
      id: string;
      decision: "confirm" | "dismiss";
    }) => {
      const path = {
        workspace_id: workspaceId,
        system_id: systemId,
        finding_id: id,
      };
      const { data, error, response } =
        decision === "confirm"
          ? await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings/{finding_id}/confirm",
              { params: { path } },
            )
          : await client.POST(
              "/api/v1/workspaces/{workspace_id}/systems/{system_id}/pii-findings/{finding_id}/dismiss",
              { params: { path } },
            );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: piiFindingsKey(workspaceId, systemId),
        }),
        queryClient.invalidateQueries({
          queryKey: sourceSummaryKey(workspaceId, systemId),
        }),
      ]),
  });
}
