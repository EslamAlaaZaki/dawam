"use client";

import { Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  ROLE_LABELS,
  allows,
  useUpdateWorkspace,
  useWorkspace,
  type Workspace,
} from "../api/workspaces";
import { Button } from "../components/ui/button";
import { Loading } from "../shell/Loading";
import { DetailsFields, readDetails } from "./DetailsFields";
import { DataWarehouseSetup } from "./DataWarehouseSetup";
import { AssistantPanel } from "./AssistantPanel";
import { FailedChecksPanel, type AssistantRequest } from "./FailedChecksPanel";
import { ConnectionPanel } from "./ConnectionPanel";
import { DeletedObjectsPanel } from "./DeletedObjectsPanel";
import { LifecyclePanel } from "./LifecyclePanel";
import { LineagePanel } from "./LineagePanel";
import { KpiCatalog } from "./KpiPanels";
import { useWorkspaceFolders } from "./useWorkspaceFolders";
import { SourceErDiagram } from "./SourceErDiagram";
import { SourceSchemaPanel } from "./SourceSchemaPanel";
import { SourceSummaryPanel } from "./SourceSummaryPanel";
import { MembersPanel } from "./MembersPanel";
import { ModelPanel } from "./ModelPanel";
import { DocumentsPanel } from "./DocumentsPanel";
import { SystemDetails, SystemsPanel } from "./SourceSystemPanels";
import { StageProgressPanel } from "./StageProgressPanel";
import { WorkspaceNotFound } from "./WorkspaceNotFound";

/**
 * A Workspace's page: its folder tree, the selected folder (kept in the `folder` URL
 * parameter, so it can be linked and reloaded) and the stage progress. The Workspace's
 * own folder shows its details, editable by owners and read-only for everyone else, and
 * its members.
 */
export function WorkspacePage({ workspaceId }: { workspaceId: string }) {
  const workspace = useWorkspace(workspaceId);
  const { folder, wanted, select, warehouse, systems, systemList } =
    useWorkspaceFolders(workspaceId, workspace.data?.name ?? "");
  const [assistantOpen, setAssistantOpen] = useState(false);
  // The panel mounts the first time it opens and then stays, so a conversation in
  // progress survives closing it and moving between folders.
  const [assistantUsed, setAssistantUsed] = useState(false);
  const [request, setRequest] = useState<AssistantRequest | null>(null);

  if (workspace.isPending) {
    return <Loading />;
  }
  if (workspace.isError) {
    const error = workspace.error;
    // 404: missing, or not the user's to see. 422: not a Workspace id at all.
    if (
      error instanceof ApiError &&
      (error.status === 404 || error.status === 422)
    ) {
      return <WorkspaceNotFound />;
    }
    return (
      <p className="page" role="alert">
        Could not load the Workspace: {error.message}
      </p>
    );
  }
  // Whether the Data Warehouse is set up decides which of its folders exist, so do not
  // resolve one (and fall back to the root) before that is known.
  const inDataWarehouse = wanted === "dw" || wanted.startsWith("dw/");
  // `systems/<id>`: the folder of one Source System.
  const openSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}`,
  );
  // `systems/<id>/kpis` and `dw/kpis`: a KPI folder, of that system or of the Data Warehouse.
  const kpiSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/kpis`,
  );
  const connectionSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/connection`,
  );

  // `systems/<id>/source-schema`: a Source System's extracted Snapshots.
  const schemaSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/source-schema`,
  );

  // `systems/<id>/er-diagram`: a Source System's tables and relationships.
  const erSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/er-diagram`,
  );

  // `systems/<id>/documents`: a Source System's uploaded documents.
  const documentsSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/documents`,
  );

  // `dw/<layer>/model`: a Layer's tables.
  const modelLayer = (["staging", "core", "mart"] as const).find(
    (layer) => folder.id === `dw/${layer}/model`,
  );

  function ask(asked: Omit<AssistantRequest, "nonce">) {
    setRequest((last) => ({ ...asked, nonce: (last?.nonce ?? 0) + 1 }));
    setAssistantUsed(true);
    setAssistantOpen(true);
  }

  return (
    <>
      <div
        className={`flex flex-col gap-5 ${assistantOpen ? "lg:mr-[340px]" : ""}`}
      >
        <header className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h2 className="m-0 text-xl font-semibold tracking-[-0.01em] text-balance">
              {workspace.data.name}
            </h2>
            <p className="mt-0.5 text-sand-11">
              Your role: {ROLE_LABELS[workspace.data.role]}
            </p>
          </div>
          <Button
            className="ml-auto"
            aria-expanded={assistantOpen}
            aria-controls="assistant-panel"
            onClick={() => {
              setAssistantUsed(true);
              setAssistantOpen((open) => !open);
            }}
          >
            <Sparkles aria-hidden />
            Assistant
          </Button>
        </header>
        {workspace.data.status === "archived" && (
          <p
            role="status"
            className="rounded-lg border border-amber-5 bg-amber-3 px-3 py-2.5 text-sm"
          >
            This Workspace is archived and read-only. Exports and reading still
            work.
          </p>
        )}
        <StageProgressPanel workspaceId={workspaceId} />
        <div className="legacy-page min-w-0 rounded-lg border border-line p-5">
          {inDataWarehouse && warehouse.isPending ? (
            <Loading />
          ) : inDataWarehouse && warehouse.isError ? (
            <p role="alert">
              Could not load the Data Warehouse: {warehouse.error.message}
            </p>
          ) : folder.id === "" ? (
            <>
              <WorkspaceDetails
                workspace={workspace.data}
                reload={() => workspace.refetch()}
              />
              <MembersPanel workspace={workspace.data} />
              <LifecyclePanel workspace={workspace.data} />
              <DeletedObjectsPanel workspace={workspace.data} />
            </>
          ) : folder.id === "dw" || folder.id === "dw/setup" ? (
            <DataWarehouseSetup workspace={workspace.data} />
          ) : folder.id === "systems" ? (
            <SystemsPanel
              workspace={workspace.data}
              systems={systemList}
              onOpen={(system) => select(`systems/${system.id}`)}
            />
          ) : kpiSystem || folder.id === "dw/kpis" ? (
            <KpiCatalog
              // A different folder starts with no KPI open.
              key={folder.id}
              workspace={workspace.data}
              scope={{ systemId: kpiSystem?.id ?? null }}
              title={
                kpiSystem ? `KPIs of ${kpiSystem.name}` : "Data Warehouse KPIs"
              }
            />
          ) : documentsSystem ? (
            <DocumentsPanel
              key={folder.id}
              workspace={workspace.data}
              area={{ systemId: documentsSystem.id }}
              title="Documents"
              ownerName={documentsSystem.name}
            />
          ) : modelLayer ? (
            <ModelPanel
              // Another Layer starts with no table open.
              key={folder.id}
              workspace={workspace.data}
              layer={modelLayer}
            />
          ) : folder.id === "dw/score" ? (
            <FailedChecksPanel workspace={workspace.data} onAsk={ask} />
          ) : folder.id === "dw/lineage" ? (
            <LineagePanel workspace={workspace.data} />
          ) : folder.id === "dw/files" ? (
            <DocumentsPanel
              workspace={workspace.data}
              area="data-warehouse"
              title="Files"
              ownerName="the Data Warehouse"
            />
          ) : openSystem ? (
            <>
              <SourceSummaryPanel
                workspaceId={workspaceId}
                system={openSystem}
              />
              <SystemDetails
                workspace={workspace.data}
                system={openSystem}
                reload={() => systems.refetch()}
              />
            </>
          ) : connectionSystem ? (
            <ConnectionPanel
              workspace={workspace.data}
              system={connectionSystem}
            />
          ) : schemaSystem ? (
            <SourceSchemaPanel
              workspace={workspace.data}
              system={schemaSystem}
            />
          ) : erSystem ? (
            <SourceErDiagram
              key={folder.id}
              workspace={workspace.data}
              system={erSystem}
            />
          ) : (
            <section aria-labelledby="folder-title">
              <h3 id="folder-title">{folder.label}</h3>
              <p className="empty-state">{folder.empty}</p>
            </section>
          )}
        </div>
      </div>
      <aside
        id="assistant-panel"
        aria-label="Assistant panel"
        hidden={!assistantOpen}
        className="dawam-ui fixed top-12 right-0 bottom-0 z-20 w-[min(340px,100vw)] overflow-y-auto border-l border-line bg-sand-1 shadow-lg lg:shadow-none"
      >
        {assistantUsed && (
          <div className="legacy-page min-h-full p-4">
            <AssistantPanel
              workspace={workspace.data}
              request={request}
              onClose={() => setAssistantOpen(false)}
              context={
                openSystem
                  ? {
                      type: "source_system",
                      id: openSystem.id,
                      label: openSystem.name,
                    }
                  : {
                      type: "folder",
                      id: folder.id || "workspace",
                      label: folder.label,
                    }
              }
            />
          </div>
        )}
      </aside>
    </>
  );
}

function WorkspaceDetails({
  workspace,
  reload,
}: {
  workspace: Workspace;
  reload: () => void;
}) {
  const update = useUpdateWorkspace(workspace.id);

  if (!allows(workspace, "workspace.edit")) {
    return (
      <section aria-labelledby="details-title">
        <h3 id="details-title">Details</h3>
        <dl className="details">
          <dt>Description</dt>
          <dd>{workspace.description || "—"}</dd>
          <dt>Business domain</dt>
          <dd>{workspace.domain || "—"}</dd>
        </dl>
        <p>
          {workspace.status === "archived"
            ? "This Workspace is archived, so its details cannot be edited."
            : "Only owners can edit these details."}
        </p>
      </section>
    );
  }

  const conflict =
    update.error instanceof ApiError && update.error.status === 409;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate({
      version: workspace.version,
      ...readDetails(event.currentTarget),
    });
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form
      key={workspace.version}
      className="form"
      onSubmit={onSubmit}
      aria-labelledby="details-title"
    >
      <h3 id="details-title">Details</h3>
      <DetailsFields initial={workspace} />
      {conflict ? (
        <div className="form-error" role="alert">
          <p>
            Someone else changed this Workspace since you opened it. Reload it
            to see their changes, then edit again.
          </p>
          <button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </button>
        </div>
      ) : (
        update.isError && (
          <p className="form-error" role="alert">
            {update.error.message}
          </p>
        )
      )}
      {update.isSuccess && <p role="status">Saved.</p>}
      <div className="form-actions">
        <button type="submit" disabled={update.isPending}>
          Save
        </button>
      </div>
    </form>
  );
}
