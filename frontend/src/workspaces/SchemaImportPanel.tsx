"use client";

import { Download } from "lucide-react";
import { useState, type ReactNode } from "react";

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
import { cn } from "../lib/utils";
import { Hint, NativeSelect, labelClasses } from "./parts";

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
    <section
      aria-label="Import from template"
      className="flex flex-col rounded-pill border border-border bg-surface-raised"
    >
      <div className="flex flex-col gap-4 p-5">
        <Hint className="max-w-prose">
          Use this when DAWAM cannot reach the database, with filled-in template
          files. Nothing is read from the database by DAWAM: you run the
          queries and upload the results.
        </Hint>
        <ol className="flex flex-col">
          <Step number={1} title="Run the helper queries">
            <label className={cn(labelClasses, "max-w-xs")}>
              Database engine
              <NativeSelect
                value={engine}
                onChange={(event) =>
                  setChosen(event.target.value as HelperEngine)
                }
              >
                {HELPER_ENGINES.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <div>
              <Button asChild>
                <a
                  href={helperQueriesUrl(workspace.id, system.id, engine)}
                  download
                >
                  <Download aria-hidden />
                  Download helper queries (SQL)
                </a>
              </Button>
            </div>
          </Step>
          <Step number={2} title="Fill the template" last>
            <Hint>
              Paste or export each query result into the sheet with the same
              name. The README sheet explains every column.
            </Hint>
            <div>
              <Button asChild>
                <a
                  href={schemaImportTemplateUrl(workspace.id, system.id)}
                  download
                >
                  <Download aria-hidden />
                  Download template (XLSX)
                </a>
              </Button>
            </div>
          </Step>
        </ol>
      </div>
    </section>
  );
}

/** A numbered step of the import, joined to the next by a line. */
function Step({
  number,
  title,
  last = false,
  children,
}: {
  number: number;
  title: string;
  last?: boolean;
  children: ReactNode;
}) {
  return (
    <li
      className={cn(
        "relative grid grid-cols-[1.75rem_minmax(0,1fr)] gap-3",
        !last &&
          "pb-5 before:absolute before:top-8 before:bottom-1 before:left-3.5 before:w-px before:bg-border",
      )}
    >
      <span className="grid size-7 place-items-center rounded-full border border-border-strong bg-surface-raised text-xs font-semibold tabular-nums">
        {number}
      </span>
      <div className="flex min-w-0 flex-col gap-2.5 pt-0.5">
        <h3 className="text-base font-medium">{title}</h3>
        {children}
      </div>
    </li>
  );
}
