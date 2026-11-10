"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

export const SIDEBAR_DEFAULT = 240;
export const SIDEBAR_MIN = 200;
export const SIDEBAR_MAX = 400;
const STEP = 16;
const STORAGE_KEY = "dawam.sidebar-width";

const clamp = (width: number) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(width)));

function readStored(): number {
  try {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY));
    return Number.isFinite(stored) && stored > 0 ? clamp(stored) : SIDEBAR_DEFAULT;
  } catch {
    return SIDEBAR_DEFAULT;
  }
}

function store(width: number) {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(width));
  } catch {
    // Storage is blocked or full: the width just isn't remembered.
  }
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

// A width that storage can't hold still applies for this page view.
let session: number | null = null;

/** The sidebar's width (240 by default, 200 to 400), remembered per browser. */
export function useSidebarWidth() {
  const width = useSyncExternalStore(subscribe, () => session ?? readStored(), () => SIDEBAR_DEFAULT);
  // `persist` is false while dragging: the width is stored once, on release.
  const setWidth = useCallback((next: number, persist = true) => {
    session = clamp(next);
    if (persist) {
      store(session);
    }
    listeners.forEach((listener) => listener());
  }, []);
  return [width, setWidth] as const;
}

/**
 * The drag handle on the sidebar's inline-end edge (desktop only): drag, arrow keys
 * (16px steps) or double-click to reset.
 */
export function SidebarResizer({
  width,
  onResize,
}: {
  width: number;
  onResize: (width: number, persist?: boolean) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const start = useRef({ x: 0, width: 0, sign: 1 });

  useEffect(() => {
    if (!dragging) {
      return;
    }
    const previous = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    return () => {
      document.body.style.userSelect = previous;
    };
  }, [dragging]);

  const direction = (element: HTMLElement) =>
    window.getComputedStyle(element).direction === "rtl" ? -1 : 1;

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { x: event.clientX, width, sign: direction(event.currentTarget) };
    setDragging(true);
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (dragging) {
      onResize(start.current.width + (event.clientX - start.current.x) * start.current.sign, false);
    }
  }

  function endDrag() {
    if (dragging) {
      setDragging(false);
      onResize(width);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const sign = direction(event.currentTarget);
    if (event.key === "ArrowRight") {
      onResize(width + STEP * sign);
    } else if (event.key === "ArrowLeft") {
      onResize(width - STEP * sign);
    } else {
      return;
    }
    event.preventDefault();
  }

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize sidebar"
      aria-valuenow={width}
      aria-valuemin={SIDEBAR_MIN}
      aria-valuemax={SIDEBAR_MAX}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onResize(SIDEBAR_DEFAULT)}
      data-dragging={dragging || undefined}
      className="group dawam-ui absolute inset-y-0 -end-1.5 z-20 hidden w-3 cursor-col-resize touch-none rounded-sm outline-primary focus-visible:outline-2 focus-visible:-outline-offset-2 lg:block"
    >
      <span
        aria-hidden
        className="absolute inset-y-0 start-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover:bg-primary group-focus-visible:bg-primary group-data-dragging:bg-primary"
      />
    </div>
  );
}
