"use client";

import { Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { ROLE_LABELS, useWorkspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { EmptyState, ErrorText, Hint, Panel, h3Classes } from "./parts";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Loading } from "../shell/Loading";
import { DataWarehouseSetup } from "./DataWarehouseSetup";
import { AssistantPanel } from "./AssistantPanel";
import { FailedChecksPanel, type AssistantRequest } from "./FailedChecksPanel";
import { ConnectionPanel } from "./ConnectionPanel";
import { DeletedObjectsPanel } from "./DeletedObjectsPanel";
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
import { WorkspaceSettingsDialog } from "./WorkspaceSettingsDialog";
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
  // The folder whose setup dialog was closed: it stays closed until another folder is
  // selected or the folder's button reopens it.
  const [setupDismissed, setSetupDismissed] = useState<string | null>(null);
  useEffect(() => setSetupDismissed(null), [folder.id]);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);
  // Opening moves focus into the panel; closing hands it back to the toggle button.
  useEffect(() => {
    if (assistantOpen) {
      panelRef.current?.focus();
    } else if (wasOpen.current) {
      toggleRef.current?.focus();
    }
    wasOpen.current = assistantOpen;
  }, [assistantOpen]);
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
    return <ErrorText>Could not load the Workspace: {error.message}</ErrorText>;
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
        className={`flex flex-col gap-5 ${assistantOpen ? "lg:mr-[min(680px,55vw)]" : ""}`}
      >
        <header className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h2 className="text-2xl font-semibold tracking-[-0.01em] text-balance">
              {workspace.data.name}
            </h2>
            <p className="mt-0.5 text-sand-11">
              Your role: {ROLE_LABELS[workspace.data.role]}
            </p>
          </div>
          <Button
            ref={toggleRef}
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
          <WorkspaceSettingsDialog
            workspace={workspace.data}
            reload={() => workspace.refetch()}
          />
        </header>
        {workspace.data.status === "archived" && (
          <p
            role="status"
            className="rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2.5 text-sm"
          >
            This Workspace is archived and read-only. Exports and reading still
            work.
          </p>
        )}
        <StageProgressPanel workspaceId={workspaceId} />
        <Dialog
          open={
            (folder.id === "dw" || folder.id === "dw/setup") &&
            setupDismissed !== folder.id
          }
          onOpenChange={(open) => setSetupDismissed(open ? null : folder.id)}
        >
          <DialogContent className="max-h-[calc(100%-2rem)] w-[min(640px,calc(100%-2rem))]">
            <DialogHeader>
              <DialogTitle className="sr-only">Data Warehouse</DialogTitle>
              <DialogDescription className="sr-only">
                Set up or edit the Data Warehouse.
              </DialogDescription>
            </DialogHeader>
            <DataWarehouseSetup workspace={workspace.data} />
          </DialogContent>
        </Dialog>
        <div className="flex min-w-0 flex-col gap-6">
          {inDataWarehouse && warehouse.isPending ? (
            <Loading />
          ) : inDataWarehouse && warehouse.isError ? (
            <ErrorText>
              Could not load the Data Warehouse: {warehouse.error.message}
            </ErrorText>
          ) : folder.id === "" ? (
            <>
              <MembersPanel workspace={workspace.data} />
              <DeletedObjectsPanel workspace={workspace.data} />
            </>
          ) : folder.id === "dw" || folder.id === "dw/setup" ? (
            <div className="flex flex-col items-start gap-3">
              <Hint>
                The Data Warehouse setup opens in a dialog: platform, schemas,
                naming rules and the date dimension.
              </Hint>
              <Button onClick={() => setSetupDismissed(null)}>
                Open Data Warehouse setup
              </Button>
            </div>
          ) : folder.id === "systems" ? (
            <SystemsPanel
              workspace={workspace.data}
              systems={systemList}
              onOpen={(system) => select(`systems/${system.id}`)}
              reload={() => systems.refetch()}
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
            <Panel aria-labelledby="folder-title">
              <h3 id="folder-title" className={h3Classes}>
                {folder.label}
              </h3>
              <EmptyState>{folder.empty}</EmptyState>
            </Panel>
          )}
        </div>
      </div>
      <aside
        id="assistant-panel"
        ref={panelRef}
        tabIndex={-1}
        hidden={!assistantOpen}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setAssistantOpen(false);
          }
        }}
        className="dawam-ui fixed top-12 right-0 bottom-0 z-20 w-[min(680px,100vw)] overflow-hidden border-l outline-none border-line bg-sand-1 shadow-lg lg:w-[min(680px,55vw)] lg:shadow-none"
      >
        {assistantUsed && (
          <div className="h-full">
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
