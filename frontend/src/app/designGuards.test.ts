import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// DESIGN.md rules that hold across the whole frontend, checked on the source.
// Vitest runs from frontend/ (jsdom gives import.meta.url a non-file scheme).
const SRC = join(process.cwd(), "src");
// The palette lives in theme.css; the generated API types aren't UI.
const SKIP = new Set(["app/theme.css", "api/schema.d.ts"]);
// The AI assistant: its panel, its message parts and its entry point.
const ASSISTANT = new Set([
  "workspaces/AssistantPanel.tsx",
  "workspaces/chatParts.tsx",
  "workspaces/WorkspacePage.tsx",
]);

const sources = (readdirSync(SRC, { recursive: true }) as string[])
  .map((file) => file.replaceAll("\\", "/"))
  .filter((file) => /\.(tsx?|css)$/.test(file))
  .filter((file) => !/\.test\.tsx?$/.test(file) && !file.startsWith("test/"))
  .filter((file) => !SKIP.has(file));

const read = (file: string) => readFileSync(join(SRC, file), "utf8");

describe("design tokens", () => {
  it.each(sources)("%s has no raw colour", (file) => {
    const source = read(file);
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(source).not.toMatch(/\b(?:rgb|hsl|oklch)a?\(/);
  });

  it.each(sources)("%s has no arbitrary colour, spacing, radius or font size", (file) => {
    // Layout math such as w-[min(440px,calc(100%-2rem))] is the one exception.
    expect(read(file)).not.toMatch(
      /\b(?:p[xytbselr]?|m[xytbselr]?|gap(?:-[xy])?|space-[xy]|rounded(?:-[a-z]+)?|text|bg|border(?:-[a-z]+)?|ring|fill|stroke|shadow|outline)-\[(?!(?:min|max|calc)\()/,
    );
  });
});

describe("the AI signature", () => {
  it.each(sources.filter((file) => !ASSISTANT.has(file)))(
    "%s uses neither the ✦ mark nor the AI gradient",
    (file) => {
      const source = read(file);
      expect(source).not.toContain("✦");
      expect(source).not.toMatch(/ai-gradient|gradient\(/);
    },
  );
});
