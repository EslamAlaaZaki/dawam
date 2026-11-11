// The ER diagram's geometry (after docs/architecture/database/erd.html): card sizes, tables
// grouped in a box per Database Schema with column-major packing (most-referenced first),
// column-to-column edge curves with crow's-foot ends, and the view that fits it all. Pure.
import type { SourceSchema } from "../api/snapshots";
import { presentColumns, type ErEdge } from "./erGraph";

type Table = SourceSchema["tables"][number];
type Column = Table["columns"][number];

/** A card's header and column row heights, and its inner padding. */
export const HEAD = 30;
export const ROW = 20;
export const PAD = 10;
/** The schema box's padding and the room its label takes above the cards. */
export const BOX_PAD = 30;
export const BOX_LABEL = 22;

const CHAR = 7.4;
const TYPE_CHAR = 6.8;
const GAP_Y = 26;
const GAP_X = 70;
const BOX_GAP = 70;
const COL_MAX_H = 760;
const ROW_MAX_W = 2600;

export interface Point {
  x: number;
  y: number;
}

export interface Rect extends Point {
  w: number;
  h: number;
}

/** A card: its size, the columns it lists, how many it leaves out, and each listed column's row. */
export interface CardGeom {
  w: number;
  h: number;
  cols: Column[];
  hidden: number;
  rows: Map<string, number>;
}

export interface SchemaBox {
  name: string;
  ids: string[];
}

export interface ErLayout {
  geom: Map<string, CardGeom>;
  pos: Map<string, Point>;
  boxes: SchemaBox[];
}

/** A card's size: wide enough for its longest column name and type. */
export function measureCard(
  table: Table,
  keys: ReadonlySet<string>,
  mode: "keys" | "all",
): CardGeom {
  const all = presentColumns(table);
  const cols = mode === "all" ? all : all.filter((c) => keys.has(c.name));
  const nameW =
    Math.max(6, ...cols.map((c) => c.name.length + (c.is_protected ? 2 : 0))) * CHAR;
  const typeW = Math.max(4, ...cols.map((c) => (c.data_type ?? "").length)) * TYPE_CHAR;
  const head = (table.name.length + (table.kind === "view" ? 6 : 0) + 3) * 7.9 + 2 * PAD + 16;
  const w = Math.ceil(Math.max(head, 34 + nameW + 18 + typeW + 22 + PAD, 170));
  const hidden = all.length - cols.length;
  return {
    w,
    h: HEAD + cols.length * ROW + (hidden ? ROW : 0) + 4,
    cols,
    hidden,
    rows: new Map(cols.map((c, i) => [c.name, i])),
  };
}

/**
 * Tables in a box per Database Schema, the biggest schema first; inside a box, columns of
 * cards no taller than COL_MAX_H, the most-referenced tables first and views last.
 */
export function layoutDiagram(
  tables: readonly Table[],
  edges: readonly ErEdge[],
  geom: Map<string, CardGeom>,
): ErLayout {
  const incoming = new Map<string, number>();
  for (const edge of edges) incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1);
  const groups = new Map<string, Table[]>();
  for (const table of tables) {
    groups.set(table.db_schema, [...(groups.get(table.db_schema) ?? []), table]);
  }
  const ordered = [...groups].sort((a, b) => b[1].length - a[1].length);
  const pos = new Map<string, Point>();
  const boxes: SchemaBox[] = [];
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const [name, group] of ordered) {
    const list = [...group].sort(
      (a, b) =>
        (incoming.get(b.id) ?? 0) - (incoming.get(a.id) ?? 0) ||
        Number(a.kind === "view") - Number(b.kind === "view") ||
        a.name.localeCompare(b.name),
    );
    const cols: Table[][] = [[]];
    let colH = 0;
    for (const table of list) {
      const h = geom.get(table.id)!.h;
      if (colH > 0 && colH + h > COL_MAX_H) {
        cols.push([]);
        colH = 0;
      }
      cols[cols.length - 1]!.push(table);
      colH += h + GAP_Y;
    }
    const widths = cols.map((c) => Math.max(...c.map((t) => geom.get(t.id)!.w)));
    const heights = cols.map((c) =>
      c.reduce((sum, t) => sum + geom.get(t.id)!.h + GAP_Y, -GAP_Y),
    );
    const bw = widths.reduce((a, b) => a + b, 0) + GAP_X * (cols.length - 1) + 2 * BOX_PAD;
    const bh = Math.max(...heights) + 2 * BOX_PAD + BOX_LABEL;
    if (x > 0 && x + bw > ROW_MAX_W) {
      x = 0;
      y += rowH + BOX_GAP;
      rowH = 0;
    }
    let cx = x + BOX_PAD;
    cols.forEach((c, i) => {
      let cy = y + BOX_PAD + BOX_LABEL;
      for (const table of c) {
        pos.set(table.id, { x: cx, y: cy });
        cy += geom.get(table.id)!.h + GAP_Y;
      }
      cx += widths[i]! + GAP_X;
    });
    boxes.push({ name, ids: list.map((t) => t.id) });
    x += bw + BOX_GAP;
    rowH = Math.max(rowH, bh);
  }
  return { geom, pos, boxes };
}

/** A schema box around its cards, wherever they were dragged. */
export function boxRect(
  box: SchemaBox,
  pos: ReadonlyMap<string, Point>,
  geom: ReadonlyMap<string, CardGeom>,
): Rect {
  let x1 = Infinity;
  let y1 = Infinity;
  let x2 = -Infinity;
  let y2 = -Infinity;
  for (const id of box.ids) {
    const p = pos.get(id)!;
    const g = geom.get(id)!;
    x1 = Math.min(x1, p.x);
    y1 = Math.min(y1, p.y);
    x2 = Math.max(x2, p.x + g.w);
    y2 = Math.max(y2, p.y + g.h);
  }
  return {
    x: x1 - BOX_PAD,
    y: y1 - BOX_PAD - BOX_LABEL,
    w: x2 - x1 + 2 * BOX_PAD,
    h: y2 - y1 + 2 * BOX_PAD + BOX_LABEL,
  };
}

export interface View {
  x: number;
  y: number;
  k: number;
}

export const MIN_ZOOM = 0.15;
export const MAX_ZOOM = 2.5;

/** The view that fits the boxes in a canvas of this size; the identity before it has a size. */
export function fitView(rects: readonly Rect[], width: number, height: number): View {
  if (rects.length === 0 || width <= 0 || height <= 0) return { x: 0, y: 0, k: 1 };
  const x1 = Math.min(...rects.map((r) => r.x));
  const y1 = Math.min(...rects.map((r) => r.y));
  const x2 = Math.max(...rects.map((r) => r.x + r.w));
  const y2 = Math.max(...rects.map((r) => r.y + r.h));
  const k = Math.max(
    MIN_ZOOM,
    Math.min(1.1, (width - 40) / (x2 - x1), (height - 40) / (y2 - y1)),
  );
  return {
    k,
    x: (width - (x2 - x1) * k) / 2 - x1 * k,
    y: (height - (y2 - y1) * k) / 2 - y1 * k,
  };
}

/** Zoom by a factor about a point of the canvas, keeping that point still. */
export function zoomAt(view: View, factor: number, cx: number, cy: number): View {
  const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, view.k * factor));
  return {
    k,
    x: cx - (cx - view.x) * (k / view.k),
    y: cy - (cy - view.y) * (k / view.k),
  };
}

/** The view that centres a card in a canvas of this size. */
export function centreOn(
  view: View,
  card: Rect,
  width: number,
  height: number,
): View {
  const k = Math.min(Math.max(view.k, 0.85), 1.3);
  return {
    k,
    x: width / 2 - (card.x + card.w / 2) * k,
    y: height / 2 - (card.y + Math.min(card.h, 300) / 2) * k,
  };
}

/** Where an edge meets a card: the side (1 right, -1 left) at the column's row. */
function anchor(p: Point, g: CardGeom, column: string | undefined, side: number): Point {
  const i = column === undefined ? undefined : g.rows.get(column);
  return {
    x: side > 0 ? p.x + g.w : p.x,
    y: i === undefined ? p.y + HEAD / 2 : p.y + HEAD + i * ROW + ROW / 2,
  };
}

/** Which sides an edge leaves and enters by: facing sides, or the same side when cards overlap. */
function sides(pa: Point, ga: CardGeom, pb: Point, gb: CardGeom, self: boolean): [number, number] {
  if (self) return [1, 1];
  if (pa.x + ga.w + 20 < pb.x) return [1, -1];
  if (pb.x + gb.w + 20 < pa.x) return [-1, 1];
  return pa.x + ga.w / 2 <= pb.x + gb.w / 2 ? [1, 1] : [-1, -1];
}

function curve(s: Point, sa: number, t: Point, sb: number): string {
  if (sa === sb) {
    const out = (sa > 0 ? Math.max(s.x, t.x) : Math.min(s.x, t.x)) + sa * (50 + Math.abs(s.y - t.y) * 0.05);
    return `M${s.x} ${s.y}C${out} ${s.y} ${out} ${t.y} ${t.x} ${t.y}`;
  }
  const d = Math.max(50, Math.abs(t.x - s.x) / 2);
  return `M${s.x} ${s.y}C${s.x + sa * d} ${s.y} ${t.x + sb * d} ${t.y} ${t.x} ${t.y}`;
}

export type GlyphKind = "many" | "one" | "zero-one";

/** A crow's-foot end: its strokes, and the circle of "zero or one". */
export function glyph(p: Point, dir: number, kind: GlyphKind): { d: string; circle?: Point } {
  const { x, y } = p;
  const o = (v: number) => x + dir * v;
  if (kind === "many") {
    return {
      d: `M${o(14)} ${y}L${x} ${y - 6}M${o(14)} ${y}L${x} ${y}M${o(14)} ${y}L${x} ${y + 6}M${o(18)} ${y - 6}V${y + 6}`,
    };
  }
  if (kind === "one") {
    return { d: `M${o(6)} ${y - 6}V${y + 6}M${o(10)} ${y - 6}V${y + 6}` };
  }
  return { d: `M${o(6)} ${y - 6}V${y + 6}`, circle: { x: o(14), y } };
}

export interface EdgeShape {
  path: string;
  from: { d: string; circle?: Point };
  to: { d: string; circle?: Point };
  label: Point;
}

/**
 * An edge from the first referencing column to the first referenced one: "many" (or "zero
 * or one" when one to one) at the referencing end, "exactly one" (or "zero or one" when
 * optional) at the referenced end.
 */
export function edgeShape(
  edge: ErEdge,
  pos: ReadonlyMap<string, Point>,
  geom: ReadonlyMap<string, CardGeom>,
): EdgeShape {
  const pa = pos.get(edge.from)!;
  const ga = geom.get(edge.from)!;
  const pb = pos.get(edge.to)!;
  const gb = geom.get(edge.to)!;
  const [sa, sb] = sides(pa, ga, pb, gb, edge.from === edge.to);
  const s = anchor(pa, ga, edge.fromCols[0], sa);
  const t = anchor(pb, gb, edge.toCols[0], sb);
  return {
    path: curve(s, sa, t, sb),
    from: glyph(s, sa, edge.oneToOne ? "zero-one" : "many"),
    to: glyph(t, sb, edge.optional ? "zero-one" : "one"),
    label: { x: (s.x + t.x) / 2, y: (s.y + t.y) / 2 - 5 },
  };
}

/** Cardinality as the relationships table and the details dialog write it. */
export const cardinality = (edge: ErEdge, incoming = false) =>
  edge.oneToOne ? "1:1" : incoming ? "1:N" : "N:1";
