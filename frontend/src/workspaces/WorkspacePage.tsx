"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import { useDataWarehouse } from "../api/dataWarehouse";
import {
  ROLE_LABELS,
  allows,
  useUpdateWorkspace,
  useWorkspace,
  type Workspace,
} from "../api/workspaces";
import { useSourceSystems } from "../api/systems";
import { Loading } from "../shell/Loading";
import { DetailsFields, readDetails } from "./DetailsFields";
import { DataWarehouseSetup } from "./DataWarehouseSetup";
import { ConnectionPanel } from "./ConnectionPanel";
import { FolderTree } from "./FolderTree";
import { JobsPanel } from "./JobsPanel";
import { LifecyclePanel } from "./LifecyclePanel";
import { KpiCatalog } from "./KpiPanels";
import { findFolder, workspaceFolders } from "./folders";
import { SourceSchemaPanel } from "./SourceSchemaPanel";
import { MembersPanel } from "./MembersPanel";
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
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const warehouse = useDataWarehouse(workspaceId);
  const systems = useSourceSystems(workspaceId);

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
  const systemList = systems.data ?? [];
  const root = workspaceFolders(
    workspace.data.name,
    warehouse.data?.set_up ?? false,
    systemList,
  );
  const requested = searchParams.get("folder") ?? "";
  // The setup step becomes the Data Warehouse folder itself once it is done, so a
  // link (or the page right after saving) to it still lands in the Data Warehouse.
  const wanted =
    warehouse.data?.set_up && requested === "dw/setup" ? "dw" : requested;
  const folder = findFolder(root, wanted) ?? root;
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

  // `systems/<id>/documents`: a Source System's uploaded documents.
  const documentsSystem = systemList.find(
    (system) => folder.id === `systems/${system.id}/documents`,
  );

  function select(id: string) {
    router.push(
      id === ""
        ? pathname
        : `${pathname}?${new URLSearchParams({ folder: id })}`,
    );
  }

  return (
    <section className="page">
      <p>
        <Link href="/">All Workspaces</Link>
      </p>
      <h2>{workspace.data.name}</h2>
      <p>Your role: {ROLE_LABELS[workspace.data.role]}</p>
      {workspace.data.status === "archived" && (
        <p className="archived-banner" role="status">
          This Workspace is archived and read-only. Exports and reading still
          work.
        </p>
      )}
      <div className="workspace-layout">
        <nav aria-label="Folders">
          <FolderTree root={root} selected={folder.id} onSelect={select} />
        </nav>
        <div className="workspace-main">
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
          ) : folder.id === "dw/files" ? (
            <DocumentsPanel
              workspace={workspace.data}
              area="data-warehouse"
              title="Files"
              ownerName="the Data Warehouse"
            />
          ) : openSystem ? (
            <SystemDetails
              workspace={workspace.data}
              system={openSystem}
              reload={() => systems.refetch()}
            />
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
          ) : (
            <section aria-labelledby="folder-title">
              <h3 id="folder-title">{folder.label}</h3>
              <p className="empty-state">{folder.empty}</p>
            </section>
          )}
          <StageProgressPanel workspaceId={workspaceId} />
          <JobsPanel workspace={workspace.data} />
        </div>
      </div>
    </section>
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
