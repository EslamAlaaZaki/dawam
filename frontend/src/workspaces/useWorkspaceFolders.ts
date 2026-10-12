"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useDataWarehouse } from "../api/dataWarehouse";
import { useSourceSystems } from "../api/systems";
import { findFolder, workspaceFolders, type Folder } from "./folders";

/**
 * A Workspace's folder tree and the selected folder, which lives in the `folder` URL
 * parameter (so it can be linked and reloaded). Shared by the sidebar's tree and the page.
 */
export function useWorkspaceFolders(
  workspaceId: string,
  workspaceName: string,
) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const warehouse = useDataWarehouse(workspaceId);
  const systems = useSourceSystems(workspaceId);

  const systemList = systems.data ?? [];
  const root: Folder = workspaceFolders(
    workspaceName,
    warehouse.data?.set_up ?? false,
    systemList,
  );
  const requested = searchParams.get("folder") ?? "";
  // The setup step becomes the Data Warehouse folder itself once it is done, so a
  // link (or the page right after saving) to it still lands in the Data Warehouse.
  const wanted =
    warehouse.data?.set_up && requested === "dw/setup" ? "dw" : requested;
  const folder = findFolder(root, wanted) ?? root;

  function select(id: string) {
    router.push(
      id === ""
        ? pathname
        : `${pathname}?${new URLSearchParams({ folder: id })}`,
    );
  }

  return { root, folder, wanted, select, warehouse, systems, systemList };
}
