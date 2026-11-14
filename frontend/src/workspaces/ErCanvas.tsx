"use client";

import { Maximize, Minus, Plus, ShieldAlert } from "lucide-react";
import {
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type Ref,
} from "react";

import type { SourceSchema } from "../api/snapshots";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { PiiMarker } from "../diagrams/PiiMarker";
import { cn } from "../lib/utils";
import { keyColumns, presentColumns, qualified, type ErEdge } from "./erGraph";
import {
  HEAD,
  ROW,
  boxRect,
  centreOn,
  edgeShape,
  fitView,
  layoutDiagram,
  measureCard,
  zoomAt,
  type ErLayout,
  type Point,
  type View,
} from "./erLayout";

type Table = SourceSchema["tables"][number];

export interface ErCanvasHandle {
  /** Centre a shown table and flash it. */
  reveal: (id: string) => void;
}

/** Search matches a table's name, its Database Schema or one of its column names. */
export function matchesSearch(table: Table, query: string): boolean {
  const q = query.trim().toLowerCase();
  return (
    q === "" ||
    table.name.toLowerCase().includes(q) ||
    table.db_schema.toLowerCase().includes(q) ||
    table.columns.some((c) => c.name.toLowerCase().includes(q))
  );
}

/**
 * The ER diagram's canvas: cards in schema boxes, edges between their columns, pan (drag
 * the background), zoom (wheel or the buttons) and fit, and tables that can be dragged
 * (positions are not saved). Hovering or focusing a table highlights its edges; a click or
 * Enter opens its details.
 */
export function ErCanvas({
  ref,
  tables,
  edges,
  allEdges,
  mode,
  lines,
  query,
  selectedId,
  onOpen,
}: {
  ref?: Ref<ErCanvasHandle>;
  tables: Table[];
  edges: ErEdge[];
  /** Every relationship, for the key columns and FK markers whatever the filters. */
  allEdges: ErEdge[];
  mode: "keys" | "all";
  lines: "all" | "hover";
  query: string;
  selectedId: string | null;
  onOpen: (id: string) => void;
}) {
  const layout = useMemo(() => {
    const geom = new Map(
      tables.map((t) => [t.id, measureCard(t, keyColumns(t, allEdges), mode)]),
    );
    return layoutDiagram(tables, edges, geom);
  }, [tables, edges, allEdges, mode]);

  // Dragged positions and a zoomed or panned view last until the layout changes.
  const [dragged, setDragged] = useState<{ layout: ErLayout; pos: Map<string, Point> } | null>(
    null,
  );
  const [manual, setManual] = useState<{ layout: ErLayout; view: View } | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);

  const pos = dragged?.layout === layout ? dragged.pos : layout.pos;
  const rects = useMemo(
    () => layout.boxes.map((box) => ({ box, rect: boxRect(box, pos, layout.geom) })),
    [layout, pos],
  );
  const fitted = useMemo(
    () => fitView(rects.map((r) => r.rect), size.w, size.h),
    // Fit to the layout as laid out, not as dragged.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [layout, size],
  );
  const view = manual?.layout === layout ? manual.view : fitted;

  const canvasRef = useRef<HTMLDivElement>(null);
  const latest = useRef({ layout, pos, view, size });
  const suppressClick = useRef(false);
  const stopDrag = useRef<(() => void) | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useLayoutEffect(() => {
    latest.current = { layout, pos, view, size };
  });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setSize({ w: entry.contentRect.width, h: entry.contentRect.height });
    });
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  /** Change the view from the latest one (several wheel events may land between renders). */
  const updateView = (change: (view: View) => View) =>
    setManual((current) => {
      const now = latest.current;
      const base = current?.layout === now.layout ? current.view : now.view;
      return { layout: now.layout, view: change(base) };
    });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const box = canvas.getBoundingClientRect();
      const factor = Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0015));
      updateView((v) => zoomAt(v, factor, event.clientX - box.left, event.clientY - box.top));
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => () => clearTimeout(flashTimer.current), []);
  // A drag in progress when the canvas goes away leaves no window listeners behind.
  useEffect(() => () => stopDrag.current?.(), []);

  useImperativeHandle(ref, () => ({
    reveal(id) {
      const now = latest.current;
      const p = now.pos.get(id);
      const g = now.layout.geom.get(id);
      if (!p || !g) return;
      setManual({
        layout: now.layout,
        view: centreOn(now.view, { ...p, w: g.w, h: g.h }, now.size.w, now.size.h),
      });
      canvasRef.current?.scrollIntoView?.({ block: "center", behavior: "smooth" });
      setFlashId(id);
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlashId(null), 1300);
    },
  }));

  const zoomBy = (factor: number) =>
    updateView((v) => zoomAt(v, factor, latest.current.size.w / 2, latest.current.size.h / 2));

  function onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    suppressClick.current = false;
    const card = (event.target as Element).closest<HTMLElement>("[data-er-table]");
    const id = card?.dataset.erTable;
    const now = latest.current;
    const start = { x: event.clientX, y: event.clientY };
    const origin = id ? now.pos.get(id)! : { x: now.view.x, y: now.view.y };
    const k = now.view.k;
    if (!id) setPanning(true);
    let moved = false;
    const move = (e: PointerEvent) => {
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      if (!moved && Math.abs(dx) + Math.abs(dy) <= 3) return;
      if (!moved) {
        // Pin the view once something moves, so dragging a table does not refit around it.
        setManual({ layout: now.layout, view: now.view });
      }
      moved = true;
      if (id) {
        setDragged((current) => {
          const base = current?.layout === now.layout ? current.pos : now.layout.pos;
          const next = new Map(base);
          next.set(id, { x: origin.x + dx / k, y: origin.y + dy / k });
          return { layout: now.layout, pos: next };
        });
        setHoverId(id);
      } else {
        setManual({ layout: now.layout, view: { k, x: origin.x + dx, y: origin.y + dy } });
      }
    };
    const detach = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      stopDrag.current = null;
    };
    const end = () => {
      detach();
      setPanning(false);
      // Swallow the click that ends a drag, but not a later one (the drag may end off the card).
      suppressClick.current = moved;
      setTimeout(() => {
        suppressClick.current = false;
      }, 0);
    };
    stopDrag.current?.();
    stopDrag.current = detach;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }

  const q = query.trim().toLowerCase();
  // A hovered table a filter removed no longer highlights anything.
  const active = hoverId !== null && layout.geom.has(hoverId) ? hoverId : null;

  return (
    <div className="er-diagram relative h-[clamp(440px,70vh,780px)] overflow-hidden">
      <div
        ref={canvasRef}
        role="group"
        aria-label="ER diagram canvas"
        className="er-canvas absolute inset-0 overflow-hidden"
        data-panning={panning || undefined}
        style={{
          backgroundSize: `${20 * view.k}px ${20 * view.k}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }}
        onPointerDown={onPointerDown}
      >
        <div
          className="absolute top-0 left-0 origin-top-left"
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}
        >
          {rects.map(({ box, rect }) => (
            <div
              key={box.name}
              className="er-schema absolute"
              style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
            >
              <span className="absolute top-2 left-3.5 font-mono text-sm font-semibold text-primary">
                {box.name}
              </span>
            </div>
          ))}
          <svg
            className="absolute top-0 left-0 overflow-visible"
            width="1"
            height="1"
            aria-hidden
          >
            {edges.map((edge) => {
              const shape = edgeShape(edge, pos, layout.geom);
              const hi = active !== null && (edge.from === active || edge.to === active);
              const hidden = lines === "hover" && !hi;
              const low = active !== null && !hi && lines === "all";
              return (
                <g
                  key={edge.id}
                  className={cn(
                    edge.kind !== "declared" && "er-inferred",
                    hi && "er-hi",
                    hidden && "hidden",
                    low && "opacity-25",
                  )}
                >
                  <path className="er-line" d={shape.path} />
                  {[shape.from, shape.to].map((end, i) => (
                    <g key={i}>
                      <path className="er-glyph" d={end.d} />
                      {end.circle && (
                        <circle
                          className="er-glyph-fill"
                          cx={end.circle.x}
                          cy={end.circle.y}
                          r={3.8}
                        />
                      )}
                    </g>
                  ))}
                  {hi && (
                    <text
                      className="er-edge-label"
                      x={shape.label.x}
                      y={shape.label.y}
                      textAnchor="middle"
                    >
                      {edge.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          {tables.map((table) => (
            <ErCard
              key={table.id}
              table={table}
              layout={layout}
              at={pos.get(table.id)!}
              allEdges={allEdges}
              highlighted={table.id === active || table.id === selectedId}
              flash={table.id === flashId}
              dim={q !== "" && !matchesSearch(table, q)}
              onHover={setHoverId}
              onClick={() => {
                if (suppressClick.current) {
                  suppressClick.current = false;
                  return;
                }
                onOpen(table.id);
              }}
            />
          ))}
        </div>
      </div>
      <Legend />
      <div className="absolute right-3 bottom-3 flex flex-col overflow-hidden rounded-lg border border-border bg-surface-raised shadow-overlay">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="rounded-none"
          aria-label="Zoom in"
          onClick={() => zoomBy(1.25)}
        >
          <Plus aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="rounded-none border-t-border"
          aria-label="Zoom out"
          onClick={() => zoomBy(0.8)}
        >
          <Minus aria-hidden />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="rounded-none border-t-border"
          aria-label="Fit to screen"
          onClick={() => setManual(null)}
        >
          <Maximize aria-hidden />
        </Button>
      </div>
    </div>
  );
}

/** A table's card: its name (with the PII marker when it has Protected Columns) and columns. */
function ErCard({
  table,
  layout,
  at,
  allEdges,
  highlighted,
  flash,
  dim,
  onHover,
  onClick,
}: {
  table: Table;
  layout: ErLayout;
  at: Point;
  allEdges: ErEdge[];
  highlighted: boolean;
  flash: boolean;
  dim: boolean;
  onHover: (id: string | null) => void;
  onClick: () => void;
}) {
  const g = layout.geom.get(table.id)!;
  const protectedNames = presentColumns(table)
    .filter((c) => c.is_protected)
    .map((c) => c.name);
  const outgoing = allEdges.filter((e) => e.from === table.id);
  const marker = (column: string) => {
    const kinds = outgoing.filter((e) => e.fromCols.includes(column)).map((e) => e.kind);
    return kinds.includes("declared") ? "declared" : kinds.length > 0 ? "inferred" : null;
  };
  return (
    <div
      data-er-table={table.id}
      className={cn(
        "absolute flex cursor-pointer flex-col overflow-hidden rounded-lg border border-border-strong bg-surface-raised transition-opacity",
        highlighted && "border-primary ring-1 ring-primary",
        flash && "border-primary ring-2 ring-primary",
        dim && "opacity-20",
      )}
      style={{ left: at.x, top: at.y, width: g.w, height: g.h }}
      onMouseEnter={() => onHover(table.id)}
      onMouseLeave={() => onHover(null)}
      onClick={onClick}
    >
      <div
        className="box-border flex shrink-0 items-center gap-1 border-b border-border bg-surface px-2.5"
        style={{ height: HEAD }}
      >
        <button
          type="button"
          aria-haspopup="dialog"
          className="min-w-0 cursor-pointer truncate rounded-sm font-mono text-mono font-semibold text-foreground focus-visible:outline-2 focus-visible:outline-primary"
          onFocus={() => onHover(table.id)}
          onBlur={() => onHover(null)}
        >
          <span aria-hidden>{table.name}</span>
          <span className="sr-only">{qualified(table)}</span>
        </button>
        {protectedNames.length > 0 && (
          <PiiMarker
            label={`${protectedNames.length} Protected ${protectedNames.length === 1 ? "Column" : "Columns"}: ${protectedNames.join(", ")}`}
          />
        )}
        {table.kind === "view" && (
          <span className="ms-auto text-2xs font-medium text-subtle-foreground">VIEW</span>
        )}
      </div>
      {g.cols.map((column) => {
        const fk = marker(column.name);
        return (
          <div
            key={column.id}
            className="flex shrink-0 items-center gap-1.5 px-2"
            style={{ height: ROW }}
            title={`${column.name} ${column.data_type ?? ""}${column.is_nullable ? " NULL" : " NOT NULL"}${column.is_protected ? " (Protected Column)" : ""}`}
          >
            <span className="w-5 shrink-0 text-2xs font-semibold">
              {column.is_pk ? (
                <span className="text-tag-pk">PK</span>
              ) : fk ? (
                <span className={fk === "declared" ? "text-tag-fk" : "text-tag-mapped"}>FK</span>
              ) : null}
            </span>
            <span className="min-w-0 truncate font-mono text-xs text-foreground">
              {column.name}
            </span>
            {column.is_protected && (
              <ShieldAlert aria-hidden className="size-3 shrink-0 text-tag-pii" />
            )}
            <span className="ms-auto shrink-0 font-mono text-2xs text-subtle-foreground">
              {column.data_type}
            </span>
            <span className="w-2 shrink-0 font-mono text-2xs text-subtle-foreground">
              {column.is_nullable ? "?" : ""}
            </span>
          </div>
        );
      })}
      {g.hidden > 0 && (
        <div
          className="flex shrink-0 items-center ps-9 text-2xs text-subtle-foreground italic"
          style={{ height: ROW }}
        >
          + {g.hidden} more {g.hidden === 1 ? "column" : "columns"}
        </div>
      )}
    </div>
  );
}

/** What the lines, ends and markers mean (replaces the old hint sentence). */
function Legend() {
  const items: { glyph: React.ReactNode; text: string }[] = [
    { glyph: <path className="er-line" d="M2 6H44" />, text: "Declared foreign key" },
    {
      glyph: (
        <g className="er-inferred">
          <path className="er-line" d="M2 6H44" />
        </g>
      ),
      text: "Inferred or manually added",
    },
    {
      glyph: <path className="er-glyph" d="M2 6H44M32 6L44 1M32 6L44 11M28 1V11" />,
      text: "Many (referencing side)",
    },
    { glyph: <path className="er-glyph" d="M2 6H44M38 1V11M42 1V11" />, text: "Exactly one (NOT NULL)" },
    {
      glyph: (
        <>
          <path className="er-glyph" d="M2 6H34M42 1V11" />
          <circle className="er-glyph-fill" cx="37" cy="6" r="3.5" />
        </>
      ),
      text: "Zero or one (nullable)",
    },
    {
      glyph: (
        <path
          className="er-legend-shield"
          d="M23 1l4 1.5v3c0 2.5-1.8 4.3-4 5-2.2-.7-4-2.5-4-5v-3z"
        />
      ),
      text: "Protected Column",
    },
  ];
  return (
    <div className="absolute bottom-2 left-2 hidden grid-cols-2 gap-x-4 gap-y-0.5 rounded-lg border border-border bg-surface-raised px-2 py-1.5 text-2xs text-muted-foreground shadow-overlay md:grid">
      {items.map((item) => (
        <div key={item.text} className="flex items-center gap-2">
          <svg
            width="31"
            height="8"
            viewBox="0 0 46 12"
            className="shrink-0 overflow-visible"
            aria-hidden
          >
            {item.glyph}
          </svg>
          <span>{item.text}</span>
        </div>
      ))}
      <div className="col-span-2 flex items-center gap-2">
        <span className="flex shrink-0 gap-1">
          <Badge variant="pk">PK</Badge>
          <Badge variant="fk">FK</Badge>
        </span>
        <span>Primary key, foreign key</span>
      </div>
    </div>
  );
}
