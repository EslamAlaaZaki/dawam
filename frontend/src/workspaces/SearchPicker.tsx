"use client";

import { Check, ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { Badge } from "../components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { cn } from "../lib/utils";
import { monoClasses } from "./parts";
import { tagVariant, type EndTag } from "./RelationshipVisual";

/** One choice: an optional key tag, a name, a small detail (the type) and a right-hand note. */
export interface PickerItem {
  id: string;
  tag?: EndTag;
  name: string;
  detail?: string;
  right?: string;
}

/** A titled group of choices, with the count shown beside its title. */
export interface PickerGroup {
  label: string;
  items: PickerItem[];
}

/** `text` with the part matching `query` in the primary colour. */
function Highlight({ text, query }: { text: string; query: string }) {
  const at = query === "" ? -1 : text.toLowerCase().indexOf(query.toLowerCase());
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="bg-transparent text-primary">{text.slice(at, at + query.length)}</mark>
      {text.slice(at + query.length)}
    </>
  );
}

/**
 * A searchable single choice on a popover: an input-like trigger, a search box, a grouped
 * listbox with keyboard movement (up, down, enter; escape closes only the popover) and a
 * footer hint. `trigger` is what the closed picker shows for the current choice.
 */
export function SearchPicker({
  label,
  trigger,
  placeholder,
  groups,
  selectedId,
  onSelect,
  open,
  onOpenChange,
  disabled = false,
  searchPlaceholder,
}: {
  label: string;
  trigger: ReactNode;
  placeholder: string;
  groups: readonly PickerGroup[];
  selectedId: string;
  onSelect: (id: string) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  disabled?: boolean;
  searchPlaceholder: string;
}) {
  const uid = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter(
          (i) => q === "" || `${i.name} ${i.detail ?? ""}`.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, query]);
  const flat = shown.flatMap((g) => g.items);
  const current = Math.min(active, Math.max(flat.length - 1, 0));
  const optionId = (index: number) => `${uid}-option-${index}`;

  // Each time the popover opens: clear the search and start on the current choice.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setQuery("");
      const chosen = groups.flatMap((g) => g.items).findIndex((i) => i.id === selectedId);
      setActive(Math.max(chosen, 0));
    }
  }

  useEffect(() => {
    listRef.current?.querySelector(`[id="${optionId(current)}"]`)?.scrollIntoView?.({
      block: "nearest",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, open]);

  function choose(id: string) {
    onSelect(id);
    onOpenChange(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(Math.min(current + 1, flat.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const item = flat[current];
      if (item) choose(item.id);
    } else if (event.key === "Escape") {
      // Closes the popover only: the dialog around it stays open.
      event.stopPropagation();
      onOpenChange(false);
    }
  }

  const indexOf = new Map(flat.map((item, i) => [item.id, i]));
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label={label}
          aria-expanded={open}
          aria-haspopup="listbox"
          disabled={disabled}
          className="flex h-9 w-full min-w-0 items-center gap-2 rounded-md border border-control-edge bg-background px-2.5 text-start text-foreground disabled:cursor-not-allowed disabled:opacity-60 data-[state=open]:border-primary data-[state=open]:ring-2 data-[state=open]:ring-primary-subtle"
        >
          <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
            {trigger ?? <span className="text-subtle-foreground">{placeholder}</span>}
          </span>
          <ChevronDown aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Search aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
          <input
            autoFocus
            role="combobox"
            aria-label={`Search ${label.toLowerCase()}`}
            aria-expanded
            aria-controls={`${uid}-list`}
            aria-activedescendant={flat.length > 0 ? optionId(current) : undefined}
            aria-autocomplete="list"
            className="h-9 w-full bg-transparent text-foreground outline-none placeholder:text-subtle-foreground"
            placeholder={searchPlaceholder}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
          />
        </div>
        <div
          ref={listRef}
          id={`${uid}-list`}
          role="listbox"
          aria-label={label}
          className="max-h-64 overflow-y-auto py-1"
        >
          {flat.length === 0 && (
            <p className="m-0 px-3 py-4 text-center text-sm text-muted-foreground">
              No match for “{query}”
            </p>
          )}
          {shown.map((group) => (
            <div key={group.label} role="group" aria-label={group.label}>
              <div className="flex items-center justify-between px-3 py-1 text-xs font-medium text-subtle-foreground">
                <span>{group.label}</span>
                <span className="tabular-nums">{group.items.length}</span>
              </div>
              {group.items.map((item) => {
                const mine = indexOf.get(item.id) ?? 0;
                return (
                  <div
                    key={item.id}
                    id={optionId(mine)}
                    role="option"
                    aria-selected={item.id === selectedId}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 px-3 py-1.5",
                      mine === current && "bg-muted",
                    )}
                    onMouseEnter={() => setActive(mine)}
                    onClick={() => choose(item.id)}
                  >
                    {item.tag && <Badge variant={tagVariant[item.tag]}>{item.tag}</Badge>}
                    <span className={cn(monoClasses, "min-w-0 flex-1 truncate")}>
                      <Highlight text={item.name} query={query.trim()} />
                    </span>
                    {(item.detail || item.right) && (
                      <span className={cn(item.detail && monoClasses, "text-xs text-subtle-foreground")}>
                        {item.detail ? (
                          <Highlight text={item.detail} query={query.trim()} />
                        ) : (
                          item.right
                        )}
                      </span>
                    )}
                    {item.id === selectedId && (
                      <Check aria-hidden className="size-4 shrink-0 text-primary" />
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="flex items-center gap-3 border-t border-border px-3 py-2 text-xs text-subtle-foreground">
          <span>
            <kbd className="font-mono">↑↓</kbd> move
          </span>
          <span>
            <kbd className="font-mono">↵</kbd> choose
          </span>
          <span>
            <kbd className="font-mono">esc</kbd> close
          </span>
        </div>
      </PopoverContent>
    </Popover>
  );
}
