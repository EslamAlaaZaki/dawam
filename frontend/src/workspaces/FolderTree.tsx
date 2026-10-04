"use client";

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
  return [row, ...folder.children.flatMap((child) => visibleRows(child, collapsed, level + 1, row))];
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
  // A folder that was showing can disappear (its parent collapsed); the tab stop must not.
  const tabStop = rows.some((r) => r.folder.id === focused) ? focused : selected;

  useEffect(() => {
    if (moveFocus.current) {
      moveFocus.current = false;
      treeRef.current?.querySelector<HTMLElement>(`[data-folder="${tabStop}"]`)?.focus();
    }
  });

  function focusOn(id: string) {
    if (id === tabStop) {
      treeRef.current?.querySelector<HTMLElement>(`[data-folder="${id}"]`)?.focus();
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
    const id = (event.target as HTMLElement).closest<HTMLElement>("[data-folder]")?.dataset.folder;
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
        className="folder-item"
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
        <span className="folder-label">
          {hasChildren && (
            <span
              className="folder-toggle"
              aria-hidden="true"
              onClick={(event) => {
                event.stopPropagation();
                setOpen(folder.id, !open);
              }}
            >
              {open ? "▾" : "▸"}
            </span>
          )}
          <span id={labelId}>{folder.label}</span>
        </span>
        {hasChildren && open && (
          <ul role="group" className="folder-group">
            {folder.children.map((child) => renderRow({ folder: child, level: level + 1, parent: row }))}
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
      className="folder-tree"
      onKeyDown={onKeyDown}
    >
      {renderRow(rows[0]!)}
    </ul>
  );
}
