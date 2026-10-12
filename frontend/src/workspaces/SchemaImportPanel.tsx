"use client";

import { useState } from "react";

import { useConnection } from "../api/connections";
import {
  HELPER_ENGINES,
  helperQueriesUrl,
  schemaImportTemplateUrl,
  type HelperEngine,
} from "../api/schemaImport";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import {
  Actions,
  Hint,
  NativeSelect,
  formCardClasses,
  h4Classes,
  labelClasses,
} from "./parts";

/**
 * The downloads of a Schema Import (spec §6.4, stories 47, 48): the Excel template and the
 * helper queries for the engine of the source database, Db2 and Teradata included. For
 * owners and editors; the queries are run on the database by the person who can reach it.
 */
export function SchemaImportPanel({
  workspace,
  system,
}: {
  workspace: Workspace;
  system: SourceSystem;
}) {
  // The Source System's own engine, when the user may see its Connection (owners).
  const connection = useConnection(
    workspace.id,
    system.id,
    allows(workspace, "connection.view"),
  );
  const [chosen, setChosen] = useState<HelperEngine | null>(null);
  const engine: HelperEngine =
    chosen ?? connection.data?.engine ?? "postgresql";
  if (!allows(workspace, "source_system.extract")) {
    return null;
  }
  return (
    <section aria-labelledby="schema-import-title" className={formCardClasses}>
      <h4 id="schema-import-title" className={h4Classes}>
        Schema Import
      </h4>
      <Hint>
        No Connection? Run the helper queries for your engine on the database,
        paste or export each result into the sheet of the same name in the
        template, and upload it.
      </Hint>
      <label className={labelClasses}>
        Database engine
        <NativeSelect
          value={engine}
          onChange={(event) => setChosen(event.target.value as HelperEngine)}
        >
          {HELPER_ENGINES.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </label>
      <Actions>
        <Button asChild>
          <a href={helperQueriesUrl(workspace.id, system.id, engine)} download>
            Download helper queries (SQL)
          </a>
        </Button>
        <Button asChild>
          <a href={schemaImportTemplateUrl(workspace.id, system.id)} download>
            Download template (XLSX)
          </a>
        </Button>
      </Actions>
    </section>
  );
}
