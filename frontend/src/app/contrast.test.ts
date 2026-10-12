// @vitest-environment node
// WCAG AA (spec #231) for the theme's colour pairs, in both themes: 4.5:1 for text, 3:1
// for the edge of a control and the focus ring (1.4.3, 1.4.11).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "theme.css"), "utf8");

function block(selector: string): Record<string, string> {
  const start = css.indexOf(`\n${selector} {`);
  const body = css.slice(start, css.indexOf("\n}", start));
  return Object.fromEntries(
    [...body.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)].map((m) => [m[1] ?? "", (m[2] ?? "").trim()]),
  );
}

const light = block(":root");
const themes = { light, dark: { ...light, ...block(".dark") } };

function resolve(theme: Record<string, string>, name: string): string {
  const value = theme[name] ?? "";
  const alias = value.match(/^var\(--([a-z0-9-]+)\)$/);
  return alias ? resolve(theme, alias[1] ?? "") : value;
}

function luminance(hex: string): number {
  const [r = 0, g = 0, b = 0] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
  const hi = Math.max(luminance(a), luminance(b));
  const lo = Math.min(luminance(a), luminance(b));
  return (hi + 0.05) / (lo + 0.05);
}

/** [foreground, background, minimum ratio] */
const PAIRS: [string, string, number][] = [
  ["sand-12", "sand-1", 4.5],
  ["sand-12", "sand-2", 4.5],
  ["sand-12", "sand-3", 4.5],
  ["sand-11", "sand-1", 4.5],
  ["sand-11", "sand-2", 4.5],
  ["sand-11", "sand-3", 4.5],
  ["sand-11", "sand-4", 4.5],
  ["jade-11", "sand-1", 4.5],
  ["jade-11", "sand-2", 4.5],
  ["pill-accent-fg", "jade-3", 4.5],
  ["pill-warning-fg", "amber-3", 4.5],
  ["red-11", "sand-1", 4.5],
  ["red-11", "red-3", 4.5],
  ["grass-11", "sand-1", 4.5],
  ["grass-11", "grass-3", 4.5],
  ["sand-12", "amber-3", 4.5],
  ["on-accent", "accent-solid", 4.5],
  ["on-accent", "accent-solid-hover", 4.5],
  ["sand-1", "sand-12", 4.5],
  ["control-edge", "sand-1", 3],
  ["toast-icon", "sand-12", 3],
  ["jade-11", "sand-4", 3],
  ["sand-10", "sand-1", 4.5],
];

describe("the theme's colour pairs", () => {
  for (const [name, theme] of Object.entries(themes)) {
    for (const [fg, bg, min] of PAIRS) {
      it(`${fg} on ${bg} is at least ${min}:1 in ${name}`, () => {
        expect(ratio(resolve(theme, fg), resolve(theme, bg))).toBeGreaterThanOrEqual(min);
      });
    }
  }
});

/** `fg` at `alpha` over `bg`, as a hex colour. */
function blend(fg: string, bg: string, alpha: number): string {
  const channel = (hex: string, i: number) => parseInt(hex.slice(i, i + 2), 16);
  return `#${[1, 3, 5]
    .map((i) =>
      Math.round(alpha * channel(fg, i) + (1 - alpha) * channel(bg, i))
        .toString(16)
        .padStart(2, "0"),
    )
    .join("")}`;
}

describe("text drawn with an opacity", () => {
  // The toast description is `text-sand-1/80` on the inverted `bg-sand-12`.
  for (const [name, theme] of Object.entries(themes)) {
    it(`the toast description keeps 4.5:1 in ${name}`, () => {
      const bg = resolve(theme, "sand-12");
      const text = blend(resolve(theme, "sand-1"), bg, 0.8);
      expect(ratio(text, bg)).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("no other component sets text colour with an opacity", () => {
    const found: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) {
          walk(path);
        } else if (name.endsWith(".tsx") && !name.endsWith(".test.tsx")) {
          for (const m of readFileSync(path, "utf8").matchAll(/text-[a-z0-9-]+\/[0-9]+/g)) {
            found.push(`${name}: ${m[0]}`);
          }
        }
      }
    };
    walk(join(__dirname, ".."));
    expect(found).toEqual(["sonner.tsx: text-sand-1/80"]);
  });
});
