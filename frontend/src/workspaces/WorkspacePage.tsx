"use client";

import {
  Crown,
  Eye,
  FolderOpen,
  Pencil,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { ApiError } from "../api/client";
import { ROLE_LABELS, useWorkspace, type WorkspaceRole } from "../api/workspaces";
import { PageHeader } from "../components/PageHeader";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";
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
import { PiiPanel } from "./PiiPanel";
import { ProfilingPanel } from "./ProfilingPanel";
import { SourceSummaryPanel } from "./SourceSummaryPanel";
import { MembersPanel } from "./MembersPanel";
import { ModelPanel } from "./ModelPanel";
import { DocumentsPanel } from "./DocumentsPanel";
import { IndexStatusPanel } from "./IndexStatusPanel";
import { SystemsPanel } from "./SourceSystemPanels";
import { StageProgressPanel } from "./StageProgressPanel";
import { WorkspaceSettingsDialog } from "./WorkspaceSettingsDialog";
import { WorkspaceNotFound } from "./WorkspaceNotFound";

const CHAT_MIN_WIDTH = 360;

/** The widest the chat may be: most of the window, leaving some of the page beside it. */
function chatMaxWidth() {
  return Math.max(CHAT_MIN_WIDTH, Math.round(window.innerWidth * 0.8));
}

function clampChatWidth(width: number) {
  return Math.min(chatMaxWidth(), Math.max(CHAT_MIN_WIDTH, Math.round(width)));
}

/** The chat's default width: 680px, at most 55% of the window. */
function defaultChatWidth() {
  return Math.round(Math.min(680, window.innerWidth * 0.55));
}

/**
 * The chat's left edge, dragged to make it wider or narrower (wide screens only; on a narrow
 * one the chat takes the whole width). The arrow keys do the same.
 */
function ChatResizer({
  width,
  onResize,
}: {
  /** Null while the chat has its default width. */
  width: number | null;
  onResize: (width: number) => void;
}) {
  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.style.userSelect = "none";
  }
  function onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      onResize(window.innerWidth - event.clientX);
    }
  }
  function onPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
      document.body.style.userSelect = "";
      onResize(window.innerWidth - event.clientX);
    }
  }
  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 96 : 32;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      onResize(
        (width ?? defaultChatWidth()) +
          (event.key === "ArrowLeft" ? step : -step),
      );
    }
  }
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the assistant"
      aria-controls="assistant-panel"
      aria-valuenow={width ?? undefined}
      aria-valuemin={CHAT_MIN_WIDTH}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
      className="absolute inset-y-0 left-0 z-10 hidden w-1.5 cursor-col-resize touch-none outline-primary transition-colors duration-120 hover:bg-border-strong focus-visible:bg-primary focus-visible:outline-2 focus-visible:outline-offset-2 lg:block"
    />
  );
}

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
  // The chat's width once the member has dragged it; null keeps the default. Not kept
  // across reloads.
  const [chatWidth, setChatWidth] = useState<number | null>(null);
  const chatStyle = (
    chatWidth === null ? {} : { "--chat-width": `${chatWidth}px` }
  ) as CSSProperties;
  function resizeChat(width: number) {
    setChatWidth(clampChatWidth(width));
  }
  // The folder whose setup dialog was closed: it stays closed until another folder is
  // selected or the folder's button reopens it.
  const [setupDismissed, setSetupDismissed] = useState<string | null>(null);
  const [dismissedIn, setDismissedIn] = useState(folder.id);
  if (dismissedIn !== folder.id) {
    // A different folder was selected: adjust state during render, not in an effect.
    setDismissedIn(folder.id);
    setSetupDismissed(null);
  }
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

  // `systems/<id>/profiling`: a Source System's profiling overview.
  const profilingSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/profiling`,
  );

  // `systems/<id>/pii`: a Source System's PII findings and value scan.
  const piiSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/pii`,
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
        style={chatStyle}
        className={`flex flex-col gap-5 ${assistantOpen ? "lg:mr-[calc(var(--chat-width,min(680px,55vw)))]" : ""}`}
      >
        <PageHeader
          title={workspace.data.name}
          description={<RoleBadge role={workspace.data.role} />}
          actions={
            <>
              <Button
                ref={toggleRef}
                aria-label="Assistant"
                aria-expanded={assistantOpen}
                aria-controls="assistant-panel"
                onClick={() => {
                  setAssistantUsed(true);
                  setAssistantOpen((open) => !open);
                }}
              >
                <span
                  aria-hidden
                  className="bg-ai-gradient bg-clip-text text-transparent"
                >
                  ✦
                </span>
                Assistant
              </Button>
              <WorkspaceSettingsDialog
                workspace={workspace.data}
                reload={() => workspace.refetch()}
              />
            </>
          }
        />
        {workspace.data.status === "archived" && (
          <p
            role="status"
            className="flex items-start gap-2 rounded-pill border border-warning/40 bg-warning-subtle px-3 py-2.5 text-sm text-foreground"
          >
            <TriangleAlert
              aria-hidden
              className="mt-0.5 size-4 shrink-0 text-warning-strong"
            />
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
              hideTitle={kpiSystem != null}
            />
          ) : folder.id === "document-index" ? (
            <IndexStatusPanel workspace={workspace.data} />
          ) : documentsSystem ? (
            <DocumentsPanel
              key={folder.id}
              workspace={workspace.data}
              area={{ systemId: documentsSystem.id }}
              title="Documents"
              hideTitle
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
            <SourceSummaryPanel
              workspace={workspace.data}
              system={openSystem}
              reload={() => systems.refetch()}
              onOpen={(child) => select(`systems/${openSystem.id}/${child}`)}
            />
          ) : connectionSystem ? (
            <ConnectionPanel
              workspace={workspace.data}
              system={connectionSystem}
            />
          ) : schemaSystem ? (
            <SourceSchemaPanel
              key={folder.id}
              workspace={workspace.data}
              system={schemaSystem}
            />
          ) : erSystem ? (
            <SourceErDiagram
              key={folder.id}
              workspace={workspace.data}
              system={erSystem}
            />
          ) : profilingSystem ? (
            <ProfilingPanel
              key={folder.id}
              workspace={workspace.data}
              system={profilingSystem}
              empty={folder.empty}
            />
          ) : piiSystem ? (
            <PiiPanel
              key={folder.id}
              workspace={workspace.data}
              system={piiSystem}
            />
          ) : (
            <Panel aria-labelledby="folder-title">
              <EmptyState
                className="border-0"
                icon={<FolderOpen />}
                title={
                  <h3 id="folder-title" className={h3Classes}>
                    {folder.label}
                  </h3>
                }
              >
                {folder.empty}
              </EmptyState>
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
        style={chatStyle}
        className="dawam-ui fixed top-12 right-0 bottom-0 z-20 w-[min(680px,100vw)] overflow-hidden border-l border-border bg-background shadow-lg outline-none lg:w-[var(--chat-width,min(680px,55vw))] lg:shadow-none"
      >
        <ChatResizer width={chatWidth} onResize={resizeChat} />
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

const ROLE_BADGES: Record<WorkspaceRole, { icon: LucideIcon; can: string }> = {
  owner: {
    icon: Crown,
    can: "Can do everything here, including members and settings.",
  },
  editor: {
    icon: Pencil,
    can: "Can change the analysis and models, but not members or settings.",
  },
  viewer: { icon: Eye, can: "Can read everything here but not change it." },
};

/** The user's role in the Workspace, with what it allows as a tooltip. */
function RoleBadge({ role }: { role: WorkspaceRole }) {
  const { icon: Icon, can } = ROLE_BADGES[role];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          role="note"
          tabIndex={0}
          aria-label={`Your role: ${ROLE_LABELS[role]}`}
          className="cursor-default"
        >
          <Icon aria-hidden />
          {ROLE_LABELS[role]}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{can}</TooltipContent>
    </Tooltip>
  );
}
