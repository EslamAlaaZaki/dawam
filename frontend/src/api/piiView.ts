// The PII view (spec §6.13): the DW columns derived from confirmed PII. The star diagram
// marks them.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type PiiViewReport = components["schemas"]["PiiViewReport"];

export const piiViewKey = (workspaceId: string) =>
  ["workspace", workspaceId, "pii-view"] as const;

export function usePiiView(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: piiViewKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(
        "/api/v1/workspaces/{workspace_id}/data-warehouse/pii",
        { params: { path: { workspace_id: workspaceId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** What the PII view says of a node: derived from confirmed PII (value edges) or only
 * influenced by it (join, filter, lookup); `pseudonymised` is hashed or fed by hashed data. */
export type PiiMark = { kind: "derived" | "influenced"; pseudonymised: boolean };
export type PiiMarks = ReadonlyMap<string, PiiMark>;

/** Shared, so an unmarked diagram does not get a new Map each render. */
export const NO_PII: PiiMarks = new Map();

export const piiMarkLabel = (m: PiiMark) =>
  `PII-${m.kind}${m.pseudonymised ? ", pseudonymised" : ""}`;

/**
 * The PII view as marks by node id (columns and tables of any kind it lists). If the view
 * cannot be loaded there are no marks and no error: diagrams just draw unmarked.
 */
export function usePiiMarks(workspaceId: string): PiiMarks {
  const { data } = usePiiView(workspaceId);
  return useMemo(() => {
    if (!data) {
      return NO_PII;
    }
    const marks = new Map<string, PiiMark>();
    for (const t of data.tables) {
      marks.set(t.id, { kind: "influenced", pseudonymised: false });
    }
    for (const c of data.columns) {
      marks.set(c.id, { kind: "derived", pseudonymised: c.pseudonymised });
    }
    return marks;
  }, [data]);
}
