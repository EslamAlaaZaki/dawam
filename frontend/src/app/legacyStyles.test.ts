// @vitest-environment node
// The redesign (spec #231) replaced the old hand-written stylesheet with Tailwind and shared
// components. This guards against its class names creeping back.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      return sources(path);
    }
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

const code = sources(SRC).map((path) => ({
  path,
  text: readFileSync(path, "utf8"),
}));

const LEGACY = [
  "legacy-page",
  "page",
  "page-heading",
  "button",
  "form",
  "form-actions",
  "details",
  "empty-state",
  "assistant",
  "assistant-job",
  "assistant-header",
  "assistant-bar",
  "assistant-turns",
  "assistant-turn",
  "assistant-question",
  "assistant-answer",
  "assistant-note",
  "assistant-failure",
  "assistant-tools",
  "assistant-form",
];

describe("the redesign's styles", () => {
  it("leave no old global stylesheet behind", () => {
    expect(existsSync(join(SRC, "app", "globals.css"))).toBe(false);
    expect(code.filter(({ text }) => text.includes("globals.css"))).toEqual([]);
  });

  it("use none of the old class names", () => {
    const users = code.flatMap(({ path, text }) =>
      [...text.matchAll(/class(?:Name)?=(?:"([^"]*)"|\{`([^`]*)`\})/g)]
        .flatMap((match) => (match[1] ?? match[2] ?? "").split(/\s+/))
        .filter((token) => LEGACY.includes(token))
        .map((token) => `${path.slice(SRC.length)}: ${token}`),
    );
    expect(users).toEqual([]);
  });

  it("style only diagram classes some component uses", () => {
    const css = readFileSync(join(SRC, "app", "theme.css"), "utf8");
    const own = new Set(
      [...css.matchAll(/\.dawam-flow \.((?:lineage|star|pii)-[a-z-]+)/g)].map(
        (match) => match[1] ?? "",
      ),
    );
    const unused = [...own].filter(
      (name) => !code.some(({ text }) => text.includes(name)),
    );
    expect(unused).toEqual([]);
  });
});
