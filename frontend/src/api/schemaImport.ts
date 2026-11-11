import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";
import { snapshotsKey, sourceSchemaKey } from "./snapshots";
import { sourceSummaryKey } from "./sourceSummary";

// Schema Import downloads (spec §6.4, stories 47, 48): the Excel template and, per
// engine, the helper queries an Editor runs on a database DAWAM cannot reach.

/** Engines with helper queries: DAWAM can connect to the first five; Db2 and Teradata
 *  only ever come in through a Schema Import. */
export const HELPER_ENGINES = [
  { id: "postgresql", label: "PostgreSQL" },
  { id: "mysql", label: "MySQL" },
  { id: "mariadb", label: "MariaDB" },
  { id: "sqlserver", label: "SQL Server" },
  { id: "oracle", label: "Oracle" },
  { id: "db2", label: "IBM Db2 (LUW)" },
  { id: "teradata", label: "Teradata" },
] as const;

export type HelperEngine = (typeof HELPER_ENGINES)[number]["id"];

const importPath = (workspaceId: string, systemId: string) =>
  `/api/v1/workspaces/${workspaceId}/systems/${systemId}/import`;

/** Where an owner or editor downloads the Schema Import template (XLSX). */
export function schemaImportTemplateUrl(workspaceId: string, systemId: string): string {
  return `${importPath(workspaceId, systemId)}/template`;
}

/** Where an owner or editor downloads an engine's helper queries (a SQL script). */
export function helperQueriesUrl(
  workspaceId: string,
  systemId: string,
  engine: HelperEngine,
): string {
  return `${importPath(workspaceId, systemId)}/helper-queries?engine=${engine}`;
}

export type ImportReport = components["schemas"]["ImportReport"];
export type ImportOutcome = components["schemas"]["ImportOutcome"];

const VALIDATE_PATH =
  "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/validate" as const;
const UPLOAD_PATH =
  "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/upload" as const;
const REQUEST_PATH =
  "/api/v1/workspaces/{workspace_id}/systems/{system_id}/import/connection-requests" as const;

/** The upload's multipart body; the generated type names the `files` field `string[]`. */
function multipart(fields: Record<string, string>, files: File[]) {
  return {
    body: { files: files as unknown as string[], accept_warnings: false },
    bodySerializer: () => {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      for (const [name, value] of Object.entries(fields)) form.append(name, value);
      return form;
    },
  };
}

/** Check a filled template (one .xlsx, or one CSV per sheet) without saving anything. */
export function useValidateSchemaImport(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (files: File[]): Promise<ImportReport> => {
      const { data, error, response } = await client.POST(VALIDATE_PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
        ...multipart({}, files),
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/**
 * Save the upload as a Snapshot with origin `import`. Not imported (errors, or warnings not
 * accepted) is an outcome, not an error; fails with `unsupported_engine` (422).
 */
export function useUploadSchemaImport(workspaceId: string, systemId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      files: File[];
      acceptWarnings: boolean;
      engine: HelperEngine;
    }): Promise<ImportOutcome> => {
      const { data, error, response } = await client.POST(UPLOAD_PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
        ...multipart(
          {
            accept_warnings: String(input.acceptWarnings),
            engine: input.engine,
          },
          input.files,
        ),
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (outcome) => {
      if (!outcome.imported) return;
      for (const queryKey of [
        snapshotsKey(workspaceId, systemId),
        sourceSchemaKey(workspaceId, systemId),
        sourceSummaryKey(workspaceId, systemId),
        ["workspace", workspaceId, "systems", systemId, "import-status"],
      ]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    },
  });
}

/** Ask the Workspace's owners to add a live Connection; resolves to how many were notified. */
export function useRequestConnection(workspaceId: string, systemId: string) {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (): Promise<number> => {
      const { data, error, response } = await client.POST(REQUEST_PATH, {
        params: { path: { workspace_id: workspaceId, system_id: systemId } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.owners_notified;
    },
  });
}
