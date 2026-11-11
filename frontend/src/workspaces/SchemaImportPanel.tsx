"use client";

import {
  Bell,
  CircleCheck,
  CircleX,
  Download,
  FileSpreadsheet,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { useState, type DragEvent, type ReactNode } from "react";

import { useConnection } from "../api/connections";
import { useImportStatus } from "../api/profiling";
import {
  HELPER_ENGINES,
  helperQueriesUrl,
  schemaImportTemplateUrl,
  useRequestConnection,
  useUploadSchemaImport,
  useValidateSchemaImport,
  type HelperEngine,
  type ImportReport,
} from "../api/schemaImport";
import type { SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { cn } from "../lib/utils";
import {
  Callout,
  ErrorText,
  Hint,
  NativeSelect,
  checkClasses,
  checkLabelClasses,
  labelClasses,
} from "./parts";

/**
 * A Schema Import (spec §6.4, stories 47, 48) in three steps: the helper queries for the
 * engine of the source database (Db2 and Teradata included), the Excel template, and the
 * upload of the filled template, checked before it is imported. For owners and editors; the
 * queries are run on the database by the person who can reach it.
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
  const importStatus = useImportStatus(workspace.id, system.id);
  const validate = useValidateSchemaImport(workspace.id, system.id);
  const upload = useUploadSchemaImport(workspace.id, system.id);
  const requestConnection = useRequestConnection(workspace.id, system.id);
  const [chosen, setChosen] = useState<HelperEngine | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [accept, setAccept] = useState(false);
  const [unchanged, setUnchanged] = useState(false);
  const engine: HelperEngine =
    chosen ?? connection.data?.engine ?? "postgresql";
  if (!allows(workspace, "source_system.extract")) {
    return null;
  }

  const canAsk =
    !allows(workspace, "connection.manage") &&
    importStatus.data?.has_connection === false;
  const blocked =
    report !== null &&
    (report.errors.length > 0 || (report.warnings.length > 0 && !accept));
  const busy = validate.isPending || upload.isPending;

  function choose(next: File[]) {
    setFiles(next);
    setReport(null);
    setAccept(false);
    setUnchanged(false);
    validate.reset();
    upload.reset();
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    const dropped = Array.from(event.dataTransfer.files);
    if (dropped.length > 0) choose(dropped);
  }

  function check() {
    setUnchanged(false);
    upload.reset();
    validate.mutate(files, { onSuccess: setReport });
  }

  function runImport() {
    setUnchanged(false);
    validate.reset();
    upload.mutate(
      { files, acceptWarnings: accept, engine },
      {
        onSuccess: (outcome) => {
          setReport(outcome.report);
          if (!outcome.imported) return;
          if (outcome.unchanged) {
            setUnchanged(true);
            return;
          }
          toast.success(
            `Imported. A new Snapshot was saved: ${outcome.report.table_count} tables and views, ${outcome.report.column_count} columns.`,
          );
          choose([]);
        },
      },
    );
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
          <Step number={2} title="Fill the template">
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
          <Step number={3} title="Upload and import" last>
            {files.length === 0 ? (
              <label
                onDragOver={(event) => event.preventDefault()}
                onDrop={onDrop}
                className="flex cursor-pointer flex-col items-center gap-2 rounded-pill border border-dashed border-border-strong bg-surface p-5 text-center text-sm text-muted-foreground focus-within:outline-2 focus-within:outline-primary"
              >
                <Upload aria-hidden className="size-6 text-subtle-foreground" />
                <span>
                  <span className="font-medium text-foreground">
                    Drop the filled template here
                  </span>{" "}
                  or click to choose
                </span>
                <span className="text-xs text-subtle-foreground">
                  One .xlsx, or one CSV per sheet named like the sheet
                </span>
                <input
                  type="file"
                  multiple
                  accept=".xlsx,.csv"
                  className="sr-only"
                  onChange={(event) => {
                    const picked = Array.from(event.target.files ?? []);
                    if (picked.length > 0) choose(picked);
                  }}
                />
              </label>
            ) : (
              <>
                <ul className="flex flex-col gap-1.5">
                  {files.map((file) => (
                    <li
                      key={file.name}
                      className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                    >
                      <FileSpreadsheet
                        aria-hidden
                        className="size-4 shrink-0 text-muted-foreground"
                      />
                      <span className="min-w-0 flex-1 truncate font-mono text-mono">
                        {file.name}
                      </span>
                    </li>
                  ))}
                </ul>
                {report && (
                  <ReportBox
                    report={report}
                    accept={accept}
                    onAccept={setAccept}
                  />
                )}
                {unchanged && (
                  <Callout
                    tone="success"
                    icon={<CircleCheck aria-hidden />}
                    role="status"
                  >
                    No differences from the latest Snapshot; nothing new was
                    saved.
                  </Callout>
                )}
                {validate.isError && (
                  <ErrorText>{validate.error.message}</ErrorText>
                )}
                {upload.isError && <ErrorText>{upload.error.message}</ErrorText>}
                <div className="flex flex-wrap gap-2">
                  <Button type="button" disabled={busy} onClick={check}>
                    Check file
                  </Button>
                  <Button
                    type="button"
                    variant="primary"
                    disabled={busy || blocked}
                    onClick={runImport}
                  >
                    Import
                  </Button>
                  <Button type="button" disabled={busy} onClick={() => choose([])}>
                    Remove
                  </Button>
                </div>
              </>
            )}
          </Step>
        </ol>
        {canAsk && (
          <Callout icon={<Bell aria-hidden />}>
            <span>
              Can DAWAM reach this database after all? Ask an owner to add a
              live connection.
            </span>
            <div>
              <Button
                type="button"
                disabled={requestConnection.isPending}
                onClick={() =>
                  requestConnection.mutate(undefined, {
                    onSuccess: (count) =>
                      toast.success(`${count} owners notified.`),
                  })
                }
              >
                Ask owners to add a live connection
              </Button>
            </div>
            {requestConnection.isError && (
              <ErrorText>{requestConnection.error.message}</ErrorText>
            )}
          </Callout>
        )}
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

/** The result of checking an upload: counts, then the errors (which block) and warnings. */
function ReportBox({
  report,
  accept,
  onAccept,
}: {
  report: ImportReport;
  accept: boolean;
  onAccept: (accepted: boolean) => void;
}) {
  const hasErrors = report.errors.length > 0;
  const hasWarnings = report.warnings.length > 0;
  const counts: [number, string][] = [
    [report.schema_count, "schemas"],
    [report.table_count, "tables and views"],
    [report.column_count, "columns"],
    [report.routine_count, "routines"],
  ];
  return (
    <Callout
      tone={hasErrors ? "danger" : hasWarnings ? "warning" : "success"}
      icon={
        hasErrors ? (
          <CircleX aria-hidden />
        ) : hasWarnings ? (
          <TriangleAlert aria-hidden />
        ) : (
          <CircleCheck aria-hidden />
        )
      }
      role="status"
    >
      <span className="font-medium">
        {report.errors.length} {report.errors.length === 1 ? "error" : "errors"}{" "}
        · {report.warnings.length}{" "}
        {report.warnings.length === 1 ? "warning" : "warnings"}
      </span>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground tabular-nums">
        {counts.map(([count, label]) => (
          <span key={label}>
            <b className="font-medium text-foreground">{count}</b> {label}
          </span>
        ))}
      </div>
      <IssueList
        issues={report.errors}
        label="Errors"
        tone="text-danger-strong"
      />
      <IssueList
        issues={report.warnings}
        label="Warnings"
        tone="text-warning-strong"
      />
      {hasWarnings && !hasErrors && (
        <label className={checkLabelClasses}>
          <input
            type="checkbox"
            className={checkClasses}
            checked={accept}
            onChange={(event) => onAccept(event.target.checked)}
          />
          Import anyway, keeping the warnings
        </label>
      )}
    </Callout>
  );
}

function IssueList({
  issues,
  label,
  tone,
}: {
  issues: ImportReport["errors"];
  label: string;
  tone: string;
}) {
  if (issues.length === 0) return null;
  return (
    <ul aria-label={label} className="flex flex-col gap-1">
      {issues.map((issue, index) => (
        <li key={index} className="flex flex-wrap gap-x-2">
          {(issue.sheet || issue.row) && (
            <span className={cn("font-mono text-mono", tone)}>
              {[issue.sheet, issue.row ? `row ${issue.row}` : null]
                .filter(Boolean)
                .join(" · ")}
            </span>
          )}
          <span>{issue.message}</span>
        </li>
      ))}
    </ul>
  );
}
