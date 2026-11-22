// The Data Warehouse score's failed checks (spec §6.11, story 123).
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type FailedCheck = components["schemas"]["FailedCheck"];

export type Score = components["schemas"]["Score"];

function useScoreQuery<T>(workspaceId: string, select: (score: Score) => T) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["workspace", workspaceId, "score"],
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/score",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    select,
  });
}

const failedChecksOf = (score: Score) => score.failed_checks;
const wholeScore = (score: Score) => score;

export function useFailedChecks(workspaceId: string) {
  return useScoreQuery(workspaceId, failedChecksOf);
}

/** The latest Data Warehouse score: overall, per Layer (staging is scored apart), per table. */
export function useScore(workspaceId: string) {
  return useScoreQuery(workspaceId, wholeScore);
}
