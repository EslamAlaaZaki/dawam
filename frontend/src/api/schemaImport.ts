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
