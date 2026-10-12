"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { Folder } from "./folders";

interface Row {
  folder: Folder;
  level: number;
  parent: Row | undefined;
}

/** The rows that are showing: a collapsed folder hides what is below it. */
function visibleRows(
  folder: Folder,
  collapsed: ReadonlySet<string>,
  level = 1,
  parent?: Row,
): Row[] {
  const row: Row = { folder, level, parent };
  if (collapsed.has(folder.id)) {
    return [row];
  }
  return [
    row,
    ...folder.children.flatMap((child) =>
      visibleRows(child, collapsed, level + 1, row),
    ),
  ];
}

/** A folder id and its ancestors' ids, nearest first, ending with the root (""). */
function withAncestors(id: string): string[] {
  const ids = [id];
  for (let at = id; at !== "";) {
    at = at.includes("/") ? at.slice(0, at.lastIndexOf("/")) : "";
    ids.push(at);
  }
  return ids;
}

/**
 * The Workspace's folders as a WAI-ARIA tree. One item is in the tab order (arrow keys
 * move focus within the tree); Enter or Space, or a click, selects the focused folder.
 * Selecting is the caller's business (it goes in the URL), so what is selected comes in.
 */
export function FolderTree({
  root,
  selected,
  onSelect,
}: {
  root: Folder;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const prefix = useId();
  const treeRef = useRef<HTMLUListElement>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [focused, setFocused] = useState(selected);
  // Set when a key moves focus: DOM focus follows once the new tab stop has rendered.
  const moveFocus = useRef(false);

  const rows = visibleRows(root, collapsed);
  // The focused folder, else the selected one, else the nearest ancestor of either that
  // is showing (a collapse can hide both); the tree always keeps one tab stop.
  const shown = new Set(rows.map((r) => r.folder.id));
  const tabStop =
    [...withAncestors(focused), ...withAncestors(selected)].find((id) =>
      shown.has(id),
    ) ?? rows[0]!.folder.id;

  useEffect(() => {
    if (moveFocus.current) {
      moveFocus.current = false;
      treeRef.current
        ?.querySelector<HTMLElement>(`[data-folder="${tabStop}"]`)
        ?.focus();
    }
  });

  function focusOn(id: string) {
    if (id === tabStop) {
      treeRef.current
        ?.querySelector<HTMLElement>(`[data-folder="${id}"]`)
        ?.focus();
      return;
    }
    setFocused(id);
    moveFocus.current = true;
  }

  function setOpen(id: string, open: boolean) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (open) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    const id = (event.target as HTMLElement).closest<HTMLElement>(
      "[data-folder]",
    )?.dataset.folder;
    const index = rows.findIndex((r) => r.folder.id === id);
    if (index < 0 || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    const row = rows[index]!;
    const hasChildren = row.folder.children.length > 0;
    const open = !collapsed.has(row.folder.id);
    let handled = true;
    switch (event.key) {
      case "ArrowDown":
        focusOn(rows[Math.min(index + 1, rows.length - 1)]!.folder.id);
        break;
      case "ArrowUp":
        focusOn(rows[Math.max(index - 1, 0)]!.folder.id);
        break;
      case "Home":
        focusOn(rows[0]!.folder.id);
        break;
      case "End":
        focusOn(rows[rows.length - 1]!.folder.id);
        break;
      case "ArrowRight":
        if (hasChildren && !open) {
          setOpen(row.folder.id, true);
        } else if (hasChildren) {
          focusOn(rows[index + 1]!.folder.id);
        }
        break;
      case "ArrowLeft":
        if (hasChildren && open) {
          setOpen(row.folder.id, false);
        } else if (row.parent) {
          focusOn(row.parent.folder.id);
        }
        break;
      case "Enter":
      case " ":
        onSelect(row.folder.id);
        break;
      default:
        handled = false;
    }
    if (handled) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function renderRow(row: Row) {
    const { folder, level } = row;
    const hasChildren = folder.children.length > 0;
    const open = !collapsed.has(folder.id);
    const labelId = `${prefix}-${folder.id.replaceAll("/", "-") || "root"}`;
    return (
      <li
        key={folder.id}
        role="treeitem"
        data-folder={folder.id}
        aria-labelledby={labelId}
        aria-level={level}
        aria-selected={folder.id === selected}
        aria-expanded={hasChildren ? open : undefined}
        tabIndex={folder.id === tabStop ? 0 : -1}
        className="outline-none [&[aria-selected=true]>.folder-label]:bg-sand-4 [&[aria-selected=true]>.folder-label]:font-medium [&[aria-selected=true]>.folder-label]:text-sand-12 [&:focus-visible>.folder-label]:outline-2 [&:focus-visible>.folder-label]:outline-offset-[-2px] [&:focus-visible>.folder-label]:outline-jade-11"
        onFocus={(event) => {
          if (event.target === event.currentTarget) {
            setFocused(folder.id);
          }
        }}
        onClick={(event) => {
          event.stopPropagation();
          setFocused(folder.id);
          onSelect(folder.id);
        }}
      >
        <span
          className="folder-label flex cursor-pointer items-center gap-1.5 rounded-md py-[5px] pr-2 text-sand-11 hover:bg-sand-3 hover:text-sand-12"
          style={{ paddingLeft: `${8 + (level - 1) * 16}px` }}
        >
          <span className="flex size-4 shrink-0 items-center justify-center">
            {hasChildren && (
              <span
                className="flex"
                aria-hidden="true"
                onClick={(event) => {
                  event.stopPropagation();
                  setOpen(folder.id, !open);
                  focusOn(folder.id);
                }}
              >
                {open ? (
                  <ChevronDown className="size-4" />
                ) : (
                  <ChevronRight className="size-4" />
                )}
              </span>
            )}
          </span>
          <span id={labelId} className="min-w-0 truncate">
            {folder.label}
          </span>
        </span>
        {hasChildren && open && (
          <ul role="group" className="m-0 list-none p-0">
            {folder.children.map((child) =>
              renderRow({ folder: child, level: level + 1, parent: row }),
            )}
          </ul>
        )}
      </li>
    );
  }

  return (
    <ul
      ref={treeRef}
      role="tree"
      aria-label="Workspace folders"
      className="m-0 list-none p-0"
      onKeyDown={onKeyDown}
    >
      {renderRow(rows[0]!)}
    </ul>
  );
}
