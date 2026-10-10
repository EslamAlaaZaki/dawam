/** The Workspace's folder tree (spec §1, story 29), the same for every member. */

export interface Folder {
  /** Path in the `folder` URL parameter, e.g. `dw/staging/model`; "" is the Workspace. */
  id: string;
  label: string;
  children: Folder[];
  /** What to show when the folder has nothing to show yet. */
  empty: string;
}

/** A Source System in the tree: what the folder needs of it. */
export interface SystemFolderInfo {
  id: string;
  name: string;
  code: string;
}

const SYSTEM_FOLDERS = [
  [
    "connection",
    "Connection | Schema Import",
    "How this system's metadata gets in will appear here.",
  ],
  [
    "source-schema",
    "Source Schema",
    "The Source Schema will appear here once metadata is loaded.",
  ],
  ["er-diagram", "ER diagram", "The ER diagram will appear here once metadata is loaded."],
  [
    "profiling",
    "Profiling",
    "Profiles will appear here once the data has been profiled.",
  ],
  [
    "pii",
    "PII",
    "PII findings will appear here once the system has been scanned.",
  ],
  ["documents", "Documents", "Uploaded documents and links will appear here."],
  ["kpis", "KPIs", "KPIs based on this system will appear here."],
  ["outputs", "Outputs", "Generated files will appear here."],
] as const;

function systemFolder(system: SystemFolderInfo): Folder {
  return {
    id: `systems/${system.id}`,
    label: system.name,
    empty: "",
    children: SYSTEM_FOLDERS.map(([slug, label, empty]) => ({
      id: `systems/${system.id}/${slug}`,
      label,
      children: [],
      empty,
    })),
  };
}

const LAYER_FOLDERS = ["Model", "Mappings", "Evaluation"] as const;

function layer(id: string, label: string): Folder {
  return {
    id: `dw/${id}`,
    label,
    empty: `The ${label} Layer has no model, mappings or evaluation yet.`,
    children: LAYER_FOLDERS.map((name) => ({
      id: `dw/${id}/${name.toLowerCase()}`,
      label: name,
      children: [],
      empty: `The ${label} Layer's ${name.toLowerCase()} will appear here once it exists.`,
    })),
  };
}

/**
 * `dwSetUp`: whether the Data Warehouse has been set up (spec story 87). Until then its
 * folder holds only the setup step and KPIs. `systems` are the Workspace's Source Systems.
 */
export function workspaceFolders(
  workspaceName: string,
  dwSetUp: boolean,
  systems: readonly SystemFolderInfo[] = [],
): Folder {
  return {
    id: "",
    label: workspaceName,
    empty: "",
    children: [
      {
        id: "systems",
        label: "Systems",
        children: systems.map(systemFolder),
        empty:
          "No Source Systems yet. Each Source System you add will get its own folder here.",
      },
      {
        id: "dw",
        label: "Data Warehouse",
        empty: "The Data Warehouse has nothing in it yet.",
        children: [
          ...(dwSetUp
            ? []
            : [
                {
                  id: "dw/setup",
                  label: "Set up Data Warehouse",
                  children: [],
                  empty: "",
                },
              ]),
          {
            id: "dw/kpis",
            label: "KPIs",
            children: [],
            empty:
              "No KPIs yet. Business metrics for the Data Warehouse will be listed here.",
          },
          ...(dwSetUp
            ? [
                layer("staging", "Staging"),
                layer("core", "Core"),
                layer("mart", "Mart"),
                {
                  id: "dw/lineage",
                  label: "Lineage",
                  children: [],
                  empty:
                    "Lineage appears here once there is a DW Schema to trace.",
                },
                {
                  id: "dw/score",
                  label: "Score",
                  children: [],
                  empty: "The Data Warehouse has not been scored yet.",
                },
                {
                  id: "dw/files",
                  label: "Files",
                  children: [],
                  empty:
                    "Generated and uploaded files of the Data Warehouse appear here.",
                },
                {
                  id: "dw/ddl",
                  label: "DDL",
                  children: [],
                  empty: "DDL is generated here once there is a DW Schema.",
                },
              ]
            : []),
        ],
      },
    ],
  };
}

export function findFolder(root: Folder, id: string): Folder | undefined {
  if (root.id === id) {
    return root;
  }
  for (const child of root.children) {
    const found = findFolder(child, id);
    if (found) {
      return found;
    }
  }
  return undefined;
}
