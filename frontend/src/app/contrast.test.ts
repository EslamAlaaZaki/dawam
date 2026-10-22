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
  ["foreground", "background", 4.5],
  ["foreground", "surface", 4.5],
  ["foreground", "surface-raised", 4.5],
  ["foreground", "muted", 4.5],
  ["muted-foreground", "background", 4.5],
  ["muted-foreground", "surface", 4.5],
  ["muted-foreground", "surface-raised", 4.5],
  ["muted-foreground", "muted", 4.5],
  ["subtle-foreground", "background", 4.5],
  ["subtle-foreground", "surface-raised", 4.5],
  ["primary", "background", 4.5],
  ["primary", "surface", 4.5],
  ["primary", "primary-subtle", 4.5],
  ["primary-foreground", "primary", 4.5],
  ["primary-foreground", "primary-hover", 4.5],
  ["success-strong", "background", 4.5],
  ["success-strong", "success-subtle", 4.5],
  ["warning-strong", "background", 4.5],
  ["warning-strong", "warning-subtle", 4.5],
  ["danger-strong", "background", 4.5],
  ["danger-strong", "danger-subtle", 4.5],
  ["info-strong", "background", 4.5],
  ["info-strong", "info-subtle", 4.5],
  ["foreground", "warning-subtle", 4.5],
  ["foreground", "ai-subtle", 4.5],
  ["background", "foreground", 4.5],
  ["control-edge", "background", 3],
  ["toast-icon", "foreground", 3],
  ["primary", "muted", 3],
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

describe("text drawn with an opacity", () => {
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
    expect(found).toEqual(["sonner.tsx: text-background/80"]);
  });
});
