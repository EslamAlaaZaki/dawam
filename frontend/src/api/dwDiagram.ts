// The star/snowflake diagram and bus matrix read the whole model of a Layer (stories 94, 95).
import { useQueries } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { useDwTables, type DwLayer, type DwTable } from "./dwModel";

/**
 * The tables of a Layer with their columns. A Mart fact points at Core conformed
 * dimensions, so a Mart's model includes those too.
 */
export function useLayerModel(workspaceId: string, layer: DwLayer) {
  const client = useApiClient();
  const own = useDwTables(workspaceId, layer);
  const core = useDwTables(workspaceId, "core");
  const wanted = [
    ...(own.data ?? []),
    ...(layer === "mart" ? (core.data ?? []).filter((t) => t.is_conformed) : []),
  ];
  const details = useQueries({
    queries: wanted.map((t) => ({
      queryKey: ["workspace", workspaceId, "dw-model", "table", t.id],
      queryFn: async (): Promise<DwTable> => {
        const { data, error, response } = await client.GET(
          "/api/v1/workspaces/{workspace_id}/data-warehouse/tables/{dw_table_id}",
          {
            params: { path: { workspace_id: workspaceId, dw_table_id: t.id } },
          },
        );
        if (error) {
          throw new ApiError(response.status, error.error);
        }
        return data;
      },
    })),
  });
  const failed = own.error ?? details.find((d) => d.error)?.error ?? null;
  const pending =
    own.isPending || (layer === "mart" && core.isPending) || details.some((d) => d.isPending);
  return {
    tables: details.flatMap((d) => (d.data ? [d.data] : [])),
    isPending: pending && !failed,
    error: failed,
  };
}
